#define _POSIX_C_SOURCE 200809L
#include "ec_io.h"

#include <errno.h>
#include <stdio.h>
#include <sys/io.h>
#include <time.h>

static unsigned int timeout_ms = 1000;
static volatile sig_atomic_t *stop_flag;

static uint64_t monotonic_ms(void)
{
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC, &ts) < 0)
        return 0;
    return (uint64_t)ts.tv_sec * 1000u + (uint64_t)ts.tv_nsec / 1000000u;
}

void ec_set_timeout(unsigned int milliseconds) { timeout_ms = milliseconds; }
void ec_set_stop_flag(volatile sig_atomic_t *flag) { stop_flag = flag; }

int ec_init(void)
{
    if (iopl(3) < 0) {
        perror("iopl(3) failed (run as root)");
        return -1;
    }
    return 0;
}

static int wait_status(uint16_t port, uint8_t mask, int want_set,
                       const char *what)
{
    uint64_t deadline = monotonic_ms() + timeout_ms;
    for (;;) {
        uint8_t status = inb(port);
        if (!!(status & mask) == want_set)
            return 0;
        if (stop_flag && *stop_flag) {
            errno = EINTR;
            return -1;
        }
        if (monotonic_ms() >= deadline) {
            fprintf(stderr, "timeout waiting for %s (port=0x%02x status=0x%02x)\n",
                    what, port, status);
            errno = ETIMEDOUT;
            return -1;
        }
    }
}

int ec_wait_ibf_clear(uint16_t port)
{
    return wait_status(port, EC_IBF, 0, "IBF clear");
}

int ec_wait_obf_set(uint16_t port)
{
    return wait_status(port, EC_OBF, 1, "OBF set");
}

int ec_out_wait(uint16_t port, uint8_t value)
{
    if (ec_wait_ibf_clear(port) < 0)
        return -1;
    outb(value, port);
    return ec_wait_ibf_clear(port);
}

int ec_write_selector(uint8_t selector, uint8_t data)
{
    if (ec_out_wait(EC_CMD_PORT, selector) < 0)
        return -1;
    return ec_out_wait(EC_CMD_PORT, data);
}

int ec_read_byte(uint8_t *value)
{
    if (ec_out_wait(EC_CMD_PORT, 0x04) < 0 ||
        ec_wait_obf_set(EC_CMD_PORT) < 0)
        return -1;
    *value = inb(EC_DATA_PORT);
    return 0;
}

int ec_flush_kbc(unsigned int limit)
{
    unsigned int drained = 0;
    while ((inb(KBC_CMD_PORT) & EC_OBF) && drained < limit) {
        (void)inb(KBC_DATA_PORT);
        drained++;
    }
    if (inb(KBC_CMD_PORT) & EC_OBF) {
        fprintf(stderr, "KBC output buffer did not drain after %u bytes\n", limit);
        return -1;
    }
    return (int)drained;
}
