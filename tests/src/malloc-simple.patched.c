#include "util.h"
#include <stdlib.h> 

int h = -11;
int g __attribute__((section(".output"))) = -12;
void test();

void _start() {
  test();
}

void test(){
  int* x = malloc(sizeof(int));
  *x = 2;
  h = *x;
  *x = 1;
  g = *x;
}


