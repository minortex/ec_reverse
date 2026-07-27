CC ?= cc
CFLAGS ?= -O2 -Wall -Wextra -Wpedantic
CPPFLAGS ?= -MMD -MP
LDFLAGS ?=
BUILD_DIR := ../build
TARGET := $(BUILD_DIR)/ec-flash
SOURCES := ec_flash.c ec_io.c follow.c flash_read.c flash_write.c
OBJECTS := $(SOURCES:%.c=$(BUILD_DIR)/%.o)
DEPS := $(OBJECTS:.o=.d)

.PHONY: all clean test

all: $(TARGET)

$(TARGET): $(OBJECTS)
	$(CC) $(LDFLAGS) -o $@ $(OBJECTS)

$(BUILD_DIR)/%.o: %.c | $(BUILD_DIR)
	$(CC) $(CPPFLAGS) $(CFLAGS) -c -o $@ $<

$(BUILD_DIR):
	mkdir -p $@

test: $(TARGET)
	$(TARGET) --help >/dev/null
	PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s ../tests -v

clean:
	rm -rf $(BUILD_DIR)

-include $(DEPS)
