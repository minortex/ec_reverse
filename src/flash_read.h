#ifndef FLASH_READ_H
#define FLASH_READ_H
#include <stddef.h>
#include <stdint.h>
typedef void (*flash_progress_fn)(size_t done, size_t total, void *opaque);
int flash_read_jedec_id(uint8_t id[4]);
int flash_read(uint32_t address, uint8_t *buffer, size_t length,
               flash_progress_fn progress, void *opaque);
#endif
