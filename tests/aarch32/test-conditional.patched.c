#include "util.h"

int g = -11;
void f(int* g);

void _start() {
  f(&g);
}

void f(int* g) {
  if(*g > 0) {
    if(*g > 0) {
      *g = *g + 1;
    }
  }
}
