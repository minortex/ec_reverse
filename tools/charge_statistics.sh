while true; do
  echo "百分比:"
  sudo uv run tools/i2ec_rw.py read 0x4ab
  echo "电压:"
  sudo uv run tools/i2ec_rw.py dump 0x438 2
  echo "当前累计状态"
  sudo uv run tools/i2ec_rw.py dump 0x388 2
  sleep 0.5s
  echo "-------------"
done
