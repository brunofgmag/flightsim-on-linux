#include <windows.h>

int main(void)
{
    HWND window = CreateWindowExA(0, "STATIC", "", WS_POPUP, 0, 0, 1, 1, NULL, NULL, GetModuleHandleA(NULL), NULL);
    if (!window)
        return 1;
    Sleep(INFINITE);
    return 0;
}
