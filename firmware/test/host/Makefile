# Host-side unit tests (no hardware, no ESP-IDF). Usage: make test    (make art prints the font)
CC ?= cc
CFLAGS = -std=c11 -Wall -Wextra -Werror -O1 -g \
         -I../../components/vna_core/include -I../../main

test_vna_core: test_vna_core.c ../../components/vna_core/vna_core.c
	$(CC) $(CFLAGS) -o $@ $^ -lm

test_fbtext: test_fbtext.c ../../main/fbtext.c
	$(CC) $(CFLAGS) -o $@ $^

# vna_test.c with ESP-IDF headers replaced by stubs and a scripted fake NanoVNA transport
test_vna_test: test_vna_test.c ../../main/vna_test.c ../../components/vna_core/vna_core.c
	$(CC) $(CFLAGS) -Istubs -o $@ $^ -lm

test: test_vna_core test_fbtext test_vna_test
	./test_vna_core
	./test_fbtext
	./test_vna_test

art: test_fbtext
	./test_fbtext --art

clean:
	rm -f test_vna_core test_fbtext test_vna_test
