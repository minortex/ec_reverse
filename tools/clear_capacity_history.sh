# 1. 写入容量缓存 (0x1450 = 5200 BE16)
sudo python3 i2ec_rw.py write 0x0340 0x14 --allow-write --confirm I_UNDERSTAND_I2EC_WRITES
sudo python3 i2ec_rw.py write 0x0341 0x50 --allow-write --confirm I_UNDERSTAND_I2EC_WRITES

# 2. 写入对齐的 Factor (52 = 0x34)
sudo python3 i2ec_rw.py write 0x0342 0x34 --allow-write --confirm I_UNDERSTAND_I2EC_WRITES
sudo python3 i2ec_rw.py write 0x03C0 0x34 --allow-write --confirm I_UNDERSTAND_I2EC_WRITES

# 3. 写入公开显示寄存器 (0x50 0x14 = 5200 LE16)
sudo python3 i2ec_rw.py write 0x0404 0x50 --allow-write --confirm I_UNDERSTAND_I2EC_WRITES
sudo python3 i2ec_rw.py write 0x0405 0x14 --allow-write --confirm I_UNDERSTAND_I2EC_WRITES

# 4. 挂起发布请求位
sudo python3 i2ec_rw.py write 0x03A1 0x02 --allow-write --confirm I_UNDERSTAND_I2EC_WRITES
