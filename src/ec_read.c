#define _POSIX_C_SOURCE 200809L
#include "ec_io.h"
#include "flash_read.h"
#include "follow.h"

#include <errno.h>
#include <getopt.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static volatile sig_atomic_t stopped;
static void on_signal(int sig) { (void)sig; stopped = 1; }

static void usage(FILE *out, const char *name)
{
    fprintf(out, "Usage: %s [--id] [--dump FILE|--verify FILE] [options]\n"
                 "  --address N       flash start address (default 0)\n"
                 "  --length N        bytes to read (default 0x40000)\n"
                 "  --timeout MS      per-I/O timeout (default 1000)\n"
                 "  --single-read     do not perform the second consistency read\n"
                 "  --no-reset        skip final EC reset (unsafe; default sends 0xfe)\n"
                 "Successful hardware operations normally reset the laptop on exit.\n"
                 "This program contains no erase or program operations.\n", name);
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
    FILE *fp = fopen(path, "wb"); int rc = 0;
    if (!fp) return -1;
    if (fwrite(data, 1, length, fp) != length || fflush(fp)) rc = -1;
    if (fclose(fp)) rc = -1;
    return rc;
}

static void progress(size_t done, size_t total, void *opaque)
{
    size_t *last = opaque, pct = total ? done * 100 / total : 100;
    if (pct >= *last + 10 || done == total) {
        fprintf(stderr, "\rread %zu/%zu (%zu%%)", done, total, pct);
        *last = pct;
        if (done == total) fputc('\n', stderr);
    }
}

int main(int argc, char **argv)
{
    const char *dump_path = NULL, *verify_path = NULL;
    uint32_t address = 0, length32 = 0x40000, timeout = 1000;
    int show_id = 0, double_read = 1, reset_ec = 1, rc = 1, opt;
    uint8_t *first = NULL, *second = NULL, *expected = NULL; size_t expected_len = 0;
    static const struct option options[] = {
        {"id", no_argument, 0, 'i'}, {"dump", required_argument, 0, 'd'},
        {"verify", required_argument, 0, 'v'}, {"address", required_argument, 0, 'a'},
        {"length", required_argument, 0, 'l'}, {"timeout", required_argument, 0, 't'},
        {"single-read", no_argument, 0, 's'}, {"no-reset", no_argument, 0, 'n'},
        {"help", no_argument, 0, 'h'}, {0,0,0,0}
    };
    while ((opt = getopt_long(argc, argv, "id:v:a:l:t:snh", options, NULL)) != -1) {
        switch (opt) {
        case 'i': show_id = 1; break; case 'd': dump_path = optarg; break;
        case 'v': verify_path = optarg; break; case 's': double_read = 0; break;
        case 'n': reset_ec = 0; break;
        case 'a': if (parse_u32(optarg, &address)) goto bad_args; break;
        case 'l': if (parse_u32(optarg, &length32) || !length32) goto bad_args; break;
        case 't': if (parse_u32(optarg, &timeout) || !timeout) goto bad_args; break;
        case 'h': usage(stdout, argv[0]); return 0; default: goto bad_args;
        }
    }
    if (optind != argc || (dump_path && verify_path) || (!show_id && !dump_path && !verify_path) ||
        (uint64_t)address + length32 > 0x1000000ULL) goto bad_args;
    if (verify_path) {
        if (load_file(verify_path, &expected, &expected_len) < 0) { perror(verify_path); goto cleanup; }
        if (expected_len != length32) {
            fprintf(stderr, "verify file is %zu bytes, --length is %u\n", expected_len, length32); goto cleanup;
        }
    }
    signal(SIGINT, on_signal); signal(SIGTERM, on_signal);
    ec_set_timeout(timeout); ec_set_stop_flag(&stopped);
    if (ec_init() < 0 || follow_enter() < 0) goto cleanup;
    if (show_id) {
        uint8_t id[4];
        if (flash_read_jedec_id(id) < 0) goto cleanup;
        printf("JEDEC ID: %02x %02x %02x %02x\n", id[0], id[1], id[2], id[3]);
    }
    if (dump_path || verify_path) {
        size_t marker = 0;
        first = malloc(length32); if (!first) goto cleanup;
        if (flash_read(address, first, length32, progress, &marker) < 0) goto cleanup;
        if (double_read) {
            marker = 0; second = malloc(length32); if (!second) goto cleanup;
            fprintf(stderr, "performing consistency read\n");
            if (flash_read(address, second, length32, progress, &marker) < 0) goto cleanup;
            if (memcmp(first, second, length32)) {
                size_t i; for (i = 0; i < length32 && first[i] == second[i]; i++);
                fprintf(stderr, "two reads differ at flash 0x%08x\n", address + (uint32_t)i); goto cleanup;
            }
            fprintf(stderr, "two reads are identical\n");
        }
        if (verify_path && memcmp(first, expected, length32)) {
            size_t i; for (i = 0; i < length32 && first[i] == expected[i]; i++);
            fprintf(stderr, "verify mismatch at flash 0x%08x: flash=%02x file=%02x\n",
                    address + (uint32_t)i, first[i], expected[i]); goto cleanup;
        }
        if (verify_path) printf("verify passed: %u bytes at 0x%08x\n", length32, address);
        if (dump_path && save_file(dump_path, first, length32) < 0) { perror(dump_path); goto cleanup; }
        if (dump_path) printf("saved %u bytes to %s\n", length32, dump_path);
    }
    rc = 0;
cleanup:
    /* Safe even after a partial enter; restores KBC if 0xad was sent. */
    fflush(NULL);
    ec_set_stop_flag(NULL);
    follow_exit(reset_ec);
    free(first); free(second); free(expected); return rc;
bad_args:
    usage(stderr, argv[0]); return 2;
}
