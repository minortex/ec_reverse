#define _GNU_SOURCE
#include "ec_io.h"
#include "flash_read.h"
#include "flash_write.h"
#include "follow.h"

#include <errno.h>
#include <getopt.h>
#include <fcntl.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <time.h>
#include <unistd.h>

#define WRITE_PREPARE_DELAY_SECONDS 3
#define SHUTDOWN_RESET_MARKER "/run/ec-flash-reset-on-shutdown"

static volatile sig_atomic_t stopped;
static void on_signal(int sig) { (void)sig; stopped = 1; }

static int wait_before_write(void)
{
    struct timespec remaining = {WRITE_PREPARE_DELAY_SECONDS, 0};

    fprintf(stderr,
            "EC write starts in %d seconds; release all keyboard keys now.\n",
            WRITE_PREPARE_DELAY_SECONDS);
    fflush(stderr);
    while (nanosleep(&remaining, &remaining) < 0) {
        if (errno != EINTR)
            return -1;
        if (stopped) {
            errno = EINTR;
            return -1;
        }
    }
    return 0;
}

static void usage(FILE *out, const char *name)
{
    fprintf(out, "Usage: %s [--id] [--dump FILE] [--verify FILE] [--write FILE] [options]\n"
                 "       %s --reset-only [--timeout MS]\n"
                 "  --address N       flash start address (default 0)\n"
                 "  --length N        bytes to read; write defaults to input file size\n"
                 "                     (read default 0x40000)\n"
                 "  --timeout MS      per-I/O timeout (default 1000)\n"
                 "  --single-read     do not perform the second read consistency check\n"
                 "  --reset-only      internal: reset EC from the shutdown helper\n"
                 "Write mode requires a 64 KiB-aligned address and file size, uses the\n"
                 "EFI standard path (4 KiB erase, erase verify, 256-byte program,\n"
                 "read-back verify), and rejects EFI vendor-specific flash paths.\n"
                 "A verified write arms a one-shot EC reset at final system shutdown;\n"
                 "no immediate reset is sent.\n"
                 "Dump files are atomically installed and synced before returning.\n",
            name, name);
}

static int arm_shutdown_reset(void)
{
    static const char contents[] = "armed\n";
    size_t offset = 0;
    int fd, saved_errno;

    fd = open(SHUTDOWN_RESET_MARKER,
              O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
    if (fd < 0)
        return -1;
    while (offset < sizeof(contents) - 1u) {
        ssize_t written = write(fd, contents + offset,
                                sizeof(contents) - 1u - offset);
        if (written < 0) {
            if (errno == EINTR)
                continue;
            goto fail;
        }
        if (written == 0) {
            errno = EIO;
            goto fail;
        }
        offset += (size_t)written;
    }
    if (fdatasync(fd) < 0)
        goto fail;
    if (close(fd) < 0) {
        saved_errno = errno;
        unlink(SHUTDOWN_RESET_MARKER);
        errno = saved_errno;
        return -1;
    }
    return 0;

fail:
    saved_errno = errno;
    close(fd);
    unlink(SHUTDOWN_RESET_MARKER);
    errno = saved_errno;
    return -1;
}

static int parse_u32(const char *s, uint32_t *out)
{
    char *end;
    unsigned long n;
    errno = 0; n = strtoul(s, &end, 0);
    if (errno || *s == '\0' || *end || n > UINT32_MAX) return -1;
    *out = (uint32_t)n; return 0;
}

static int load_file(const char *path, uint8_t **data, size_t *length)
{
    FILE *fp = fopen(path, "rb"); long size; size_t got;
    if (!fp) return -1;
    if (fseek(fp, 0, SEEK_END) || (size = ftell(fp)) < 0 ||
        fseek(fp, 0, SEEK_SET)) { fclose(fp); return -1; }
    *data = malloc(size ? (size_t)size : 1);
    if (!*data) { fclose(fp); return -1; }
    got = fread(*data, 1, (size_t)size, fp);
    if (got != (size_t)size || ferror(fp) || fclose(fp)) {
        free(*data); *data = NULL; return -1;
    }
    *length = got; return 0;
}

static int save_file(const char *path, const uint8_t *data, size_t length)
{
    char *temporary = NULL, *directory = NULL, *slash;
    size_t path_length = strlen(path), offset = 0;
    int fd = -1, directory_fd = -1, saved_errno, rc = -1;

    temporary = malloc(path_length + 32);
    directory = strdup(path);
    if (!temporary || !directory) goto out;
    snprintf(temporary, path_length + 32, "%s.tmp.%ld", path, (long)getpid());

    fd = open(temporary, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
    if (fd < 0) goto out;
    while (offset < length) {
        ssize_t written = write(fd, data + offset, length - offset);
        if (written < 0) {
            if (errno == EINTR) continue;
            goto out;
        }
        if (written == 0) { errno = EIO; goto out; }
        offset += (size_t)written;
    }
    if (fdatasync(fd) < 0 || rename(temporary, path) < 0)
        goto out;

    slash = strrchr(directory, '/');
    if (slash) {
        if (slash == directory) slash[1] = '\0';
        else *slash = '\0';
    } else {
        strcpy(directory, ".");
    }
    directory_fd = open(directory, O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    if (directory_fd < 0 || fsync(directory_fd) < 0 || syncfs(fd) < 0)
        goto out;
    rc = 0;
out:
    saved_errno = errno;
    if (directory_fd >= 0) close(directory_fd);
    if (fd >= 0) close(fd);
    if (rc < 0 && temporary) unlink(temporary);
    free(temporary); free(directory);
    errno = saved_errno;
    return rc;
}

struct progress_state {
    const char *label;
    size_t last;
};

static void progress(size_t done, size_t total, void *opaque)
{
    struct progress_state *state = opaque;
    size_t pct = total ? done * 100 / total : 100;
    if (pct >= state->last + 10 || done == total) {
        fprintf(stderr, "\r%s %zu/%zu (%zu%%)", state->label, done, total, pct);
        state->last = pct;
        if (done == total) fputc('\n', stderr);
    }
}

static int first_difference(const uint8_t *a, const uint8_t *b, size_t length,
                            size_t *offset)
{
    size_t i;
    for (i = 0; i < length && a[i] == b[i]; i++);
    if (i == length)
        return 0;
    *offset = i;
    return 1;
}

static int write_image(uint32_t address, const uint8_t *image, size_t length,
                       const uint8_t id[4], int *write_started)
{
    uint8_t *scratch = malloc(FLASH_ERASE_BLOCK_SIZE);
    size_t block_offset;
    if (!scratch)
        return -1;

    for (block_offset = 0; block_offset < length;
         block_offset += FLASH_ERASE_BLOCK_SIZE) {
        uint32_t block_address = address + (uint32_t)block_offset;
        struct progress_state state = {"pre-read", 0};
        size_t mismatch;

        fprintf(stderr, "block 0x%06x: checking current contents\n", block_address);
        if (flash_read(block_address, scratch, FLASH_ERASE_BLOCK_SIZE,
                       progress, &state) < 0) {
            perror("flash pre-read");
            goto fail;
        }
        if (!first_difference(scratch, image + block_offset,
                              FLASH_ERASE_BLOCK_SIZE, &mismatch)) {
            fprintf(stderr, "block 0x%06x: already identical, skipped\n",
                    block_address);
            continue;
        }
        fprintf(stderr, "block 0x%06x: first change at 0x%06x\n",
                block_address, block_address + (uint32_t)mismatch);

        /* The EFI clears status-register protection for every 64 KiB block. */
        *write_started = 1;
        if (flash_clear_protection(id) < 0) {
            perror("clear flash protection");
            goto fail;
        }

        state.label = "erase"; state.last = 0;
        *write_started = 1;
        if (flash_erase_block(block_address, progress, &state) < 0) {
            perror("flash erase");
            goto fail;
        }

        state.label = "erase verify"; state.last = 0;
        if (flash_read(block_address, scratch, FLASH_ERASE_BLOCK_SIZE,
                       progress, &state) < 0) {
            perror("erase verify read");
            goto fail;
        }
        for (mismatch = 0; mismatch < FLASH_ERASE_BLOCK_SIZE; mismatch++)
            if (scratch[mismatch] != 0xff)
                break;
        if (mismatch != FLASH_ERASE_BLOCK_SIZE) {
            fprintf(stderr,
                    "erase verify mismatch at flash 0x%06x: got %02x, expected ff\n",
                    block_address + (uint32_t)mismatch, scratch[mismatch]);
            errno = EIO;
            goto fail;
        }

        state.label = "program"; state.last = 0;
        if (flash_program_block(block_address, image + block_offset,
                                progress, &state) < 0) {
            perror("flash program");
            goto fail;
        }
        if (flash_write_disable() < 0) {
            perror("flash write disable");
            goto fail;
        }

        state.label = "program verify"; state.last = 0;
        if (flash_read(block_address, scratch, FLASH_ERASE_BLOCK_SIZE,
                       progress, &state) < 0) {
            perror("program verify read");
            goto fail;
        }
        if (first_difference(scratch, image + block_offset,
                             FLASH_ERASE_BLOCK_SIZE, &mismatch)) {
            fprintf(stderr,
                    "program verify mismatch at flash 0x%06x: flash=%02x file=%02x\n",
                    block_address + (uint32_t)mismatch, scratch[mismatch],
                    image[block_offset + mismatch]);
            errno = EIO;
            goto fail;
        }
        fprintf(stderr, "block 0x%06x: erase/program/verify passed\n",
                block_address);
    }

    free(scratch);
    return 0;
fail:
    free(scratch);
    return -1;
}

int main(int argc, char **argv)
{
    const char *dump_path = NULL, *verify_path = NULL, *write_path = NULL;
    uint32_t address = 0, length32 = 0x40000, timeout = 1000;
    int show_id = 0, double_read = 1, reset_only = 0;
    int length_set = 0;
    int write_started = 0, have_id = 0, entered = 0, rc = 1, opt;
    uint8_t id[4] = {0};
    uint8_t *first = NULL, *second = NULL, *expected = NULL, *write_data = NULL;
    size_t expected_len = 0, write_len = 0;
    enum { OPT_RESET_ONLY = 1000 };
    static const struct option options[] = {
        {"id", no_argument, 0, 'i'}, {"dump", required_argument, 0, 'd'},
        {"verify", required_argument, 0, 'v'}, {"write", required_argument, 0, 'w'},
        {"address", required_argument, 0, 'a'},
        {"length", required_argument, 0, 'l'}, {"timeout", required_argument, 0, 't'},
        {"single-read", no_argument, 0, 's'},
        {"reset-only", no_argument, 0, OPT_RESET_ONLY},
        {"help", no_argument, 0, 'h'}, {0,0,0,0}
    };
    while ((opt = getopt_long(argc, argv, "id:v:w:a:l:t:sh", options, NULL)) != -1) {
        switch (opt) {
        case 'i': show_id = 1; break; case 'd': dump_path = optarg; break;
        case 'v': verify_path = optarg; break; case 'w': write_path = optarg; break;
        case 's': double_read = 0; break;
        case OPT_RESET_ONLY:
            /* getopt_long accepts unique prefixes; reset actions require exact spelling. */
            if (strcmp(argv[optind - 1], "--reset-only") != 0)
                goto bad_args;
            reset_only = 1;
            break;
        case 'a': if (parse_u32(optarg, &address)) goto bad_args; break;
        case 'l':
            if (parse_u32(optarg, &length32) || !length32) goto bad_args;
            length_set = 1; break;
        case 't': if (parse_u32(optarg, &timeout) || !timeout) goto bad_args; break;
        case 'h': usage(stdout, argv[0]); return 0; default: goto bad_args;
        }
    }
    if (optind != argc ||
        (!show_id && !dump_path && !verify_path && !write_path && !reset_only) ||
        (write_path && (dump_path || verify_path)) ||
        (reset_only && (show_id || dump_path || verify_path || write_path ||
                        length_set || address != 0 || !double_read)))
        goto bad_args;

    if (write_path) {
        if (load_file(write_path, &write_data, &write_len) < 0) {
            perror(write_path); goto cleanup;
        }
        if (!write_len || write_len > UINT32_MAX) {
            fprintf(stderr, "write file size %zu is invalid\n", write_len);
            goto cleanup;
        }
        if (!length_set)
            length32 = (uint32_t)write_len;
        else if (write_len != length32) {
            fprintf(stderr, "write file is %zu bytes, --length is %u\n",
                    write_len, length32);
            goto cleanup;
        }
        if ((address & (FLASH_ERASE_BLOCK_SIZE - 1u)) ||
            (length32 & (FLASH_ERASE_BLOCK_SIZE - 1u))) {
            fprintf(stderr,
                    "write address and length must both be aligned to 0x%x bytes\n",
                    FLASH_ERASE_BLOCK_SIZE);
            goto cleanup;
        }
    }
    if ((uint64_t)address + length32 > 0x1000000ULL)
        goto bad_args;

    if (verify_path) {
        if (load_file(verify_path, &expected, &expected_len) < 0) {
            perror(verify_path); goto cleanup;
        }
        if (expected_len != length32) {
            fprintf(stderr, "verify file is %zu bytes, --length is %u\n",
                    expected_len, length32); goto cleanup;
        }
    }

    if (geteuid() != 0) {
        fprintf(stderr, "ec-flash must be run as root; no EC access attempted\n");
        goto cleanup;
    }

    signal(SIGINT, on_signal); signal(SIGTERM, on_signal);
    ec_set_timeout(timeout); ec_set_stop_flag(&stopped);
    /* Check iopl/CAP_SYS_RAWIO before the three-second keyboard-release delay. */
    if (ec_init() < 0)
        goto cleanup;
    if (write_path) {
        if (unlink(SHUTDOWN_RESET_MARKER) < 0 && errno != ENOENT) {
            perror("disarm previous shutdown EC reset");
            goto cleanup;
        }
        fprintf(stderr,
                "A successful verified write will arm a one-shot EC reset at final system shutdown.\n"
                "No immediate reset will be sent.\n");
        if (wait_before_write() < 0) {
            perror("write preparation delay");
            goto cleanup;
        }
    }
    if (follow_enter() < 0) goto cleanup;
    entered = 1;

    if (reset_only)
        fprintf(stderr, "requesting EFI-style EC reset at Follow Mode exit\n");

    if (show_id || write_path) {
        if (flash_read_jedec_id(id) < 0) goto cleanup;
        have_id = 1;
        printf("JEDEC ID: %02x %02x %02x %02x\n", id[0], id[1], id[2], id[3]);
    }

    if (write_path) {
        uint32_t capacity;
        if (!have_id || !flash_write_path_supported(id)) {
            fprintf(stderr,
                    "JEDEC ID %02x %02x %02x uses an unsupported EFI vendor-specific write path\n",
                    id[0], id[1], id[2]);
            goto cleanup;
        }
        if (flash_capacity_from_jedec_id(id, &capacity) < 0) {
            fprintf(stderr, "cannot derive flash capacity from JEDEC ID %02x %02x %02x\n",
                    id[0], id[1], id[2]);
            goto cleanup;
        }
        if ((uint64_t)address + length32 > capacity) {
            fprintf(stderr,
                    "write range ends at 0x%08x, beyond JEDEC capacity 0x%08x\n",
                    address + length32, capacity);
            goto cleanup;
        }
        fprintf(stderr,
                "WRITE ENABLED: %u bytes at flash 0x%08x using EFI standard path\n",
                length32, address);
        if (write_image(address, write_data, length32, id, &write_started) < 0)
            goto cleanup;
        printf("write and read-back verify passed: %u bytes at 0x%08x\n",
               length32, address);
    }

    if (dump_path || verify_path) {
        struct progress_state state = {"read", 0};
        first = malloc(length32); if (!first) goto cleanup;
        if (flash_read(address, first, length32, progress, &state) < 0) goto cleanup;
        if (double_read) {
            state.last = 0; second = malloc(length32); if (!second) goto cleanup;
            fprintf(stderr, "performing consistency read\n");
            if (flash_read(address, second, length32, progress, &state) < 0) goto cleanup;
            if (memcmp(first, second, length32)) {
                size_t i; for (i = 0; i < length32 && first[i] == second[i]; i++);
                fprintf(stderr, "two reads differ at flash 0x%08x\n",
                        address + (uint32_t)i); goto cleanup;
            }
            fprintf(stderr, "two reads are identical\n");
        }
        if (verify_path && memcmp(first, expected, length32)) {
            size_t i; for (i = 0; i < length32 && first[i] == expected[i]; i++);
            fprintf(stderr, "verify mismatch at flash 0x%08x: flash=%02x file=%02x\n",
                    address + (uint32_t)i, first[i], expected[i]); goto cleanup;
        }
        if (verify_path) printf("verify passed: %u bytes at 0x%08x\n", length32, address);
        if (dump_path && save_file(dump_path, first, length32) < 0) {
            perror(dump_path); goto cleanup;
        }
        if (dump_path) printf("saved and synced %u bytes to %s\n", length32, dump_path);
    }
    rc = 0;
cleanup:
    /* Safe after a partial enter; complete an in-flight SPI mutation if possible. */
    fflush(NULL);
    ec_set_stop_flag(NULL);
    if (entered && write_started && rc != 0) {
        fprintf(stderr, "warning: write failed; waiting for any in-flight flash operation\n");
        if (flash_wait_ready() < 0 || flash_write_disable() < 0)
            fprintf(stderr, "warning: could not confirm flash idle/write-disabled\n");
    }
    if (reset_only && rc == 0)
        sync();
    if (follow_exit(reset_only && rc == 0) < 0) {
        fprintf(stderr, "warning: EC/KBC cleanup did not complete\n");
        rc = 1;
    }
    if (rc == 0 && write_path) {
        sync();
        if (arm_shutdown_reset() < 0) {
            perror("arm shutdown EC reset");
            rc = 1;
        } else {
            fprintf(stderr,
                    "armed one-shot EC reset at shutdown via %s\n",
                    SHUTDOWN_RESET_MARKER);
        }
    }
    free(first); free(second); free(expected); free(write_data); return rc;
bad_args:
    usage(stderr, argv[0]); return 2;
}
