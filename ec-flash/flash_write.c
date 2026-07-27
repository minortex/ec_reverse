#define _POSIX_C_SOURCE 200809L
#include "flash_write.h"
#include "ec_io.h"
#include "follow.h"

#include <errno.h>
#include <stdio.h>
#include <time.h>

#define FLASH_READY_TIMEOUT_MS 120000u
#define FLASH_WEL_TIMEOUT_MS 5000u

static uint64_t monotonic_ms(void)
{
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC, &ts) < 0)
        return 0;
    return (uint64_t)ts.tv_sec * 1000u + (uint64_t)ts.tv_nsec / 1000000u;
}

static int check_active(void)
{
    if (!follow_active()) {
        errno = EPERM;
        return -1;
    }
    if (ec_stop_requested()) {
        errno = EINTR;
        return -1;
    }
    return 0;
}

static int transaction_begin(void)
{
    return ec_out_wait(EC_CMD_PORT, 0x01);
}

static int transaction_close(void)
{
    return ec_out_wait(EC_CMD_PORT, 0x05);
}

static int status_stream_begin(void)
{
    if (transaction_begin() < 0 || ec_write_selector(0x02, 0x05) < 0)
        return -1;
    return 0;
}

static int status_stream_read(uint8_t *status)
{
    return ec_read_byte(status);
}

static int wait_status(uint8_t mask, uint8_t expected, unsigned int timeout_ms,
                       const char *what)
{
    uint64_t deadline;
    uint8_t status = 0;

    if (check_active() < 0 || status_stream_begin() < 0)
        return -1;
    deadline = monotonic_ms() + timeout_ms;
    for (;;) {
        if (ec_stop_requested()) {
            errno = EINTR;
            return -1;
        }
        if (status_stream_read(&status) < 0)
            return -1;
        if ((status & mask) == expected)
            break;
        if (monotonic_ms() >= deadline) {
            fprintf(stderr, "timeout waiting for %s (SPI status=0x%02x)\n",
                    what, status);
            errno = ETIMEDOUT;
            return -1;
        }
    }
    return transaction_close();
}

int flash_write_path_supported(const uint8_t id[4])
{
    if (!id) {
        errno = EINVAL;
        return 0;
    }

    /*
     * The EFI has separate programming paths for SST/unknown devices and for
     * one old MXIC part (c2 .. 10).  This implementation intentionally uses
     * only its standard 0x20 erase + 0x02 page-program path, which includes
     * the target XMC XM25QH80 (20 40 14).
     */
    if (id[0] == 0x00 || id[0] == 0xff || id[0] == 0xbf)
        return 0;
    if (id[0] == 0xc2 && id[2] == 0x10)
        return 0;
    return 1;
}

int flash_capacity_from_jedec_id(const uint8_t id[4], uint32_t *capacity)
{
    unsigned int shift;

    if (!id || !capacity) {
        errno = EINVAL;
        return -1;
    }
    shift = id[2];
    if (shift < 16 || shift > 24) {
        errno = ENOTSUP;
        return -1;
    }
    *capacity = (uint32_t)1u << shift;
    return 0;
}

int flash_wait_ready(void)
{
    /* EFI RVA 0x27cc: RDSR, stream status bytes until WIP clears, close 0x05. */
    return wait_status(0x01, 0x00, FLASH_READY_TIMEOUT_MS, "flash ready");
}

int flash_read_status(uint8_t *status)
{
    if (!status) {
        errno = EINVAL;
        return -1;
    }
    if (check_active() < 0 || status_stream_begin() < 0 ||
        status_stream_read(status) < 0)
        return -1;
    return transaction_close();
}

static int flash_write_enable(void)
{
    if (flash_wait_ready() < 0 || transaction_begin() < 0 ||
        ec_write_selector(0x02, 0x06) < 0)
        return -1;

    /* EFI RVA 0x2a68 verifies WEL=1 and WIP=0 before every mutation. */
    return wait_status(0x03, 0x02, FLASH_WEL_TIMEOUT_MS,
                       "write-enable latch");
}

int flash_clear_protection(const uint8_t id[4])
{
    if (!flash_write_path_supported(id)) {
        errno = ENOTSUP;
        return -1;
    }
    if (check_active() < 0 || flash_write_enable() < 0 ||
        transaction_begin() < 0 || ec_write_selector(0x02, 0x01) < 0 ||
        ec_write_selector(0x03, 0x00) < 0)
        return -1;
    return flash_wait_ready();
}

int flash_erase_block(uint32_t address, flash_progress_fn progress, void *opaque)
{
    size_t offset;

    if (check_active() < 0)
        return -1;
    if ((address & (FLASH_ERASE_BLOCK_SIZE - 1u)) != 0 ||
        (uint64_t)address + FLASH_ERASE_BLOCK_SIZE > 0x1000000ULL) {
        errno = EINVAL;
        return -1;
    }

    for (offset = 0; offset < FLASH_ERASE_BLOCK_SIZE;
         offset += FLASH_ERASE_SECTOR_SIZE) {
        uint32_t current = address + (uint32_t)offset;

        /*
         * Standard EFI path: sixteen 4 KiB Sector Erase (0x20) operations,
         * rather than relying on a vendor-specific 64 KiB opcode.
         */
        if (flash_write_enable() < 0 || transaction_begin() < 0 ||
            ec_write_selector(0x02, 0x20) < 0 ||
            ec_write_selector(0x03, (current >> 16) & 0xff) < 0 ||
            ec_write_selector(0x03, (current >> 8) & 0xff) < 0 ||
            ec_write_selector(0x03, current & 0xff) < 0 ||
            transaction_close() < 0 || flash_wait_ready() < 0)
            return -1;
        if (progress)
            progress(offset + FLASH_ERASE_SECTOR_SIZE,
                     FLASH_ERASE_BLOCK_SIZE, opaque);
    }
    return 0;
}

int flash_program_block(uint32_t address, const uint8_t *data,
                        flash_progress_fn progress, void *opaque)
{
    size_t offset;

    if (check_active() < 0)
        return -1;
    if (!data || (address & (FLASH_ERASE_BLOCK_SIZE - 1u)) != 0 ||
        (uint64_t)address + FLASH_ERASE_BLOCK_SIZE > 0x1000000ULL) {
        errno = EINVAL;
        return -1;
    }

    for (offset = 0; offset < FLASH_ERASE_BLOCK_SIZE;
         offset += FLASH_PAGE_SIZE) {
        uint32_t current = address + (uint32_t)offset;
        size_t i;

        if (flash_write_enable() < 0 || transaction_begin() < 0 ||
            ec_write_selector(0x02, 0x02) < 0 ||
            ec_write_selector(0x03, (current >> 16) & 0xff) < 0 ||
            ec_write_selector(0x03, (current >> 8) & 0xff) < 0 ||
            ec_write_selector(0x03, current & 0xff) < 0)
            return -1;
        for (i = 0; i < FLASH_PAGE_SIZE; i++)
            if (ec_write_selector(0x03, data[offset + i]) < 0)
                return -1;
        if (flash_wait_ready() < 0)
            return -1;
        if (progress)
            progress(offset + FLASH_PAGE_SIZE, FLASH_ERASE_BLOCK_SIZE, opaque);
    }
    return 0;
}

int flash_write_disable(void)
{
    uint64_t deadline;
    uint8_t status = 0;

    if (flash_wait_ready() < 0 || transaction_begin() < 0 ||
        ec_write_selector(0x02, 0x04) < 0 || status_stream_begin() < 0)
        return -1;
    deadline = monotonic_ms() + FLASH_WEL_TIMEOUT_MS;
    for (;;) {
        if (ec_stop_requested()) {
            errno = EINTR;
            return -1;
        }
        if (status_stream_read(&status) < 0)
            return -1;
        if (!(status & 0x02))
            break;
        if (monotonic_ms() >= deadline) {
            fprintf(stderr,
                    "timeout waiting for write-disable latch (SPI status=0x%02x)\n",
                    status);
            errno = ETIMEDOUT;
            return -1;
        }
    }
    return transaction_close();
}
