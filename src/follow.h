#ifndef FOLLOW_H
#define FOLLOW_H
int follow_enter(void);
int follow_exit(int reset_ec);
int follow_active(void);
int follow_transaction_begin(void);
int follow_read_transaction_begin(void);
int follow_transaction_finish(void);
int follow_read_transaction_finish(void);
#endif
