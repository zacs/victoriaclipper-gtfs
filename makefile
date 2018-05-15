TARGET ?= clipper.zip

BUILD_DIR ?= ./build

$(BUILD_DIR)/$(TARGET): agency.txt calendar_dates.txt calendar.txt feed_info.txt routes.txt shapes.txt stop_times.txt stops.txt trips.txt
	$(MKDIR_P) $(dir $@)
	zip $(BUILD_DIR)/$(TARGET) *.txt

test: $(BUILD_DIR)/$(TARGET)
	pip install -r requirements.txt
	feedvalidator.py -n --output=CONSOLE $(BUILD_DIR)/$(TARGET)
  $(TEST_P) $@

clean:
	$(RM) -r $(BUILD_DIR)

MKDIR_P ?= mkdir -p
TEST_P ?= feedvalidator.py -n --output=CONSOLE
