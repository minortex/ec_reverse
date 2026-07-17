#ifndef EC_IO_H
#define EC_IO_H

#include <signal.h>
#include <stdint.h>

#define EC_CMD_PORT  0x66
#define EC_DATA_PORT 0x62
#define KBC_CMD_PORT 0x64
#define KBC_DATA_PORT 0x60
#define EC_IBF 0x02
#define EC_OBF 0x01

int ec_init(void);
void ec_set_timeout(unsigned int milliseconds);
void ec_set_stop_flag(volatile sig_atomic_t *flag);
int ec_wait_ibf_clear(uint16_t port);
int ec_wait_obf_set(uint16_t port);
int ec_out_wait(uint16_t port, uint8_t value);
int ec_write_selector(uint8_t selector, uint8_t data);
int ec_read_byte(uint8_t *value);
int ec_flush_kbc(unsigned int limit);

#endif
