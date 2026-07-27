#ifndef FLASH_WRITE_H
#define FLASH_WRITE_H

#include "flash_read.h"

#include <stddef.h>
#include <stdint.h>

#define FLASH_ERASE_BLOCK_SIZE 0x10000u
#define FLASH_ERASE_SECTOR_SIZE 0x1000u
#define FLASH_PAGE_SIZE 0x100u

int flash_write_path_supported(const uint8_t id[4]);
int flash_capacity_from_jedec_id(const uint8_t id[4], uint32_t *capacity);
int flash_wait_ready(void);
int flash_read_status(uint8_t *status);
int flash_clear_protection(const uint8_t id[4]);
int flash_erase_block(uint32_t address, flash_progress_fn progress, void *opaque);
int flash_program_block(uint32_t address, const uint8_t *data,
                        flash_progress_fn progress, void *opaque);
int flash_write_disable(void);

#endif
