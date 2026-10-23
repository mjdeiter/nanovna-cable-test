idf_component_register(SRCS "main.c" "vna_usb.c" "vna_test.c" "display.c" "lcd_tdisplay_s3.c" "fbtext.c"
                       INCLUDE_DIRS "."
                       REQUIRES vna_core
                       PRIV_REQUIRES driver esp_lcd usb)
