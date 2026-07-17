#include "follow.h"
#include "ec_io.h"

#include <stdio.h>
#include <sys/io.h>

static int in_follow;
static int kbc_disabled;
static unsigned int transaction_close_writes;

int follow_enter(void)
{
    uint8_t ack;
    if (ec_flush_kbc(256) < 0)
        return -1;
    if (ec_wait_ibf_clear(KBC_CMD_PORT) < 0)
        return -1;
    outb(0xad, KBC_CMD_PORT);
    kbc_disabled = 1;
    if (ec_wait_ibf_clear(KBC_CMD_PORT) < 0 ||
        ec_wait_ibf_clear(EC_CMD_PORT) < 0)
        return -1;
    outb(0xdc, EC_CMD_PORT);
    /* From this instruction onward cleanup must attempt top-level 0xfc. */
    in_follow = 1;
    if (ec_wait_ibf_clear(EC_CMD_PORT) < 0 ||
        ec_wait_obf_set(EC_CMD_PORT) < 0)
        return -1;
    ack = inb(EC_DATA_PORT);
    if (ack != 0x33) {
        fprintf(stderr, "follow ACK 0x%02x, expected 0x33\n", ack);
        return -1;
    }
    transaction_close_writes = 0;
    return 0;
}

int follow_transaction_begin(void)
{
    /* EFI writes 0x01 directly before the Selector=0x02/opcode pair. */
    transaction_close_writes = 2;
    return ec_out_wait(EC_CMD_PORT, 0x01);
}

int follow_read_transaction_begin(void)
{
    /* Fast Read uses the 0x27cc status handshake, not ID's two 0x05 writes. */
    transaction_close_writes = 0;
    return ec_out_wait(EC_CMD_PORT, 0x01);
}

int follow_transaction_finish(void)
{
    /*
     * ifux64.efi closes an SPI/EC sub-transaction with two direct writes of
     * 0x05 to port 0x66 (at RVAs 0x2994 and 0x29bb after JEDEC-ID reads).
     * These are distinct from the final top-level Follow exit command 0xfc.
     */
    while (transaction_close_writes) {
        if (ec_out_wait(EC_CMD_PORT, 0x05) < 0)
            return -1;
        transaction_close_writes--;
    }
    return 0;
}

int follow_read_transaction_finish(void)
{
    uint8_t status;

    /* ifux64.efi routine at RVA 0x27cc, used after each 64 KiB verify read. */
    for (;;) {
        if (ec_out_wait(EC_CMD_PORT, 0x01) < 0 ||
            ec_write_selector(0x02, 0x05) < 0 ||
            ec_out_wait(EC_CMD_PORT, 0x04) < 0 ||
            ec_wait_obf_set(EC_CMD_PORT) < 0)
            return -1;
        status = inb(EC_DATA_PORT);
        if (!(status & 0x01))
            break;
    }
    if (ec_out_wait(EC_CMD_PORT, 0x00) < 0)
        return -1;
    transaction_close_writes = 0;
    return 0;
}

int follow_exit(int reset_ec)
{
    int rc = 0;
    if (in_follow) {
        if (follow_transaction_finish() < 0)
            rc = -1;
        /* Official F.nsh passes reset=1; EFI emits 0xfe before 0xfc. */
        if (reset_ec && ec_out_wait(EC_CMD_PORT, 0xfe) < 0)
            rc = -1;
        if (ec_out_wait(EC_CMD_PORT, 0xfc) < 0)
            rc = -1;
    }
    in_follow = 0;
    transaction_close_writes = 0;
    if (kbc_disabled) {
        /* EFI drains pending keyboard bytes after 0xfc and before 0xae. */
        if (ec_flush_kbc(256) < 0)
            rc = -1;
        if (ec_out_wait(KBC_CMD_PORT, 0xae) < 0)
            rc = -1;
    }
    kbc_disabled = 0;
    return rc;
}

int follow_active(void) { return in_follow; }
