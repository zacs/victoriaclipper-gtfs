TARGET ?= clipper.zip

BUILD_DIR ?= ./build
SRC_DIR ?= ./src

$(BUILD_DIR)/$(TARGET): $(SRC_DIR)/agency.txt $(SRC_DIR)/calendar_dates.txt $(SRC_DIR)/calendar.txt $(SRC_DIR)/feed_info.txt $(SRC_DIR)/routes.txt $(SRC_DIR)/shapes.txt $(SRC_DIR)/stop_times.txt $(SRC_DIR)/stops.txt $(SRC_DIR)/trips.txt
	$(MKDIR_P) $(dir $@)
	zip $(BUILD_DIR)/$(TARGET) $(SRC_DIR)/*.txt

test: $(BUILD_DIR)/$(TARGET)
	pip install -r requirements.txt
	feedvalidator.py -n --output=CONSOLE $(BUILD_DIR)/$(TARGET)
  $(TEST_P) $@

clean:
	$(RM) -r $(BUILD_DIR)

MKDIR_P ?= mkdir -p
TEST_P ?= feedvalidator.py -n --output=CONSOLE
