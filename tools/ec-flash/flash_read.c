#include "flash_read.h"
#include "ec_io.h"
#include "follow.h"

#include <errno.h>
#include <stdio.h>

int flash_read_jedec_id(uint8_t id[4])
{
    if (!follow_active()) { errno = EPERM; return -1; }
    if (follow_transaction_begin() < 0 ||
        ec_write_selector(0x02, 0x9f) < 0)
        return -1;
    for (size_t i = 0; i < 4; i++)
        if (ec_read_byte(&id[i]) < 0)
            return -1;
    return follow_transaction_finish();
}

int flash_read(uint32_t address, uint8_t *buffer, size_t length,
               flash_progress_fn progress, void *opaque)
{
    size_t offset = 0;
    if (!follow_active()) { errno = EPERM; return -1; }
    if ((uint64_t)address + length > 0x1000000ULL) { errno = ERANGE; return -1; }

    while (offset < length) {
        uint32_t current = address + (uint32_t)offset;
        size_t to_boundary = 0x10000u - (current & 0xffffu);
        size_t chunk = length - offset > to_boundary ? to_boundary : length - offset;
        if (follow_read_transaction_begin() < 0 ||
            ec_write_selector(0x02, 0x0b) < 0 ||
            ec_write_selector(0x03, (current >> 16) & 0xff) < 0 ||
            ec_write_selector(0x03, (current >> 8) & 0xff) < 0 ||
            ec_write_selector(0x03, current & 0xff) < 0 ||
            ec_write_selector(0x03, 0x00) < 0)
            return -1;
        for (size_t i = 0; i < chunk; i++)
            if (ec_read_byte(&buffer[offset + i]) < 0)
                return -1;
        if (follow_read_transaction_finish() < 0)
            return -1;
        offset += chunk;
        if (progress)
            progress(offset, length, opaque);
    }
    return 0;
}
