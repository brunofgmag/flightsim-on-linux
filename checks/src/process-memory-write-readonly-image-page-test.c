#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define ORIGINAL 0x4f524947494e414cULL
#define HOOKED 0x484f4f4b45445054ULL
#define SYS_MPROTECT 10L
#define LINUX_PROT_READ 1L
#define LINUX_PROT_EXEC 4L
#define TEB_SYSCALL_DISPATCH 0x340

static volatile ULONGLONG slot[512] __attribute__((aligned(4096))) = {ORIGINAL};

static long raw_mprotect(void *address, long size, long prot)
{
    volatile unsigned char *selector = (volatile unsigned char *)NtCurrentTeb() + TEB_SYSCALL_DISPATCH;
    unsigned char saved = *selector;
    long ret;

    *selector = 0;
    __asm__ volatile ("syscall"
                      : "=a"(ret)
                      : "0"(SYS_MPROTECT), "D"(address), "S"(size), "d"(prot)
                      : "rcx", "r11", "memory");
    *selector = saved;
    return ret;
}

static int target(const char *event_text)
{
    HANDLE ready = (HANDLE)(ULONG_PTR)_strtoui64(event_text, NULL, 16);
    DWORD old;

    if (!VirtualProtect((void *)slot, sizeof(slot), PAGE_EXECUTE_READWRITE, &old))
        return 3;
    if (raw_mprotect((void *)slot, sizeof(slot), LINUX_PROT_READ | LINUX_PROT_EXEC))
        return 4;
    SetEvent(ready);
    Sleep(INFINITE);
    return 0;
}

static HANDLE start_target(void)
{
    SECURITY_ATTRIBUTES inherit = {sizeof(inherit), NULL, TRUE};
    STARTUPINFOA startup = {sizeof(startup)};
    PROCESS_INFORMATION info;
    char self[MAX_PATH], command[2 * MAX_PATH];
    HANDLE ready, waits[2];

    ready = CreateEventA(&inherit, TRUE, FALSE, NULL);
    GetModuleFileNameA(NULL, self, sizeof(self));
    snprintf(command, sizeof(command), "\"%s\" target %llx", self, (unsigned long long)(ULONG_PTR)ready);
    if (!CreateProcessA(NULL, command, NULL, NULL, TRUE, 0, NULL, NULL, &startup, &info))
        return NULL;
    CloseHandle(info.hThread);
    waits[0] = ready;
    waits[1] = info.hProcess;
    if (WaitForMultipleObjects(2, waits, FALSE, 30000) != WAIT_OBJECT_0)
    {
        TerminateProcess(info.hProcess, 1);
        CloseHandle(info.hProcess);
        info.hProcess = NULL;
    }
    CloseHandle(ready);
    return info.hProcess;
}

static int finish(HANDLE process, int status)
{
    TerminateProcess(process, 0);
    return status;
}

int main(int argc, char **argv)
{
    MEMORY_BASIC_INFORMATION info;
    ULONGLONG value = HOOKED, seen = 0;
    SIZE_T done = 0;
    HANDLE process;
    BOOL wrote;

    if (argc == 3 && !strcmp(argv[1], "target"))
        return target(argv[2]);

    if (!(process = start_target()))
    {
        printf("the target could not make its image page executable and read-only underneath\n");
        return 2;
    }
    if (!VirtualQueryEx(process, (void *)slot, &info, sizeof(info)) || info.Protect != PAGE_EXECUTE_WRITECOPY)
    {
        printf("the target page reports protection %08lx, not PAGE_EXECUTE_WRITECOPY\n", info.Protect);
        return finish(process, 2);
    }

    wrote = WriteProcessMemory(process, (void *)&slot[1], &value, sizeof(value), &done);
    printf("WriteProcessMemory on a PAGE_EXECUTE_WRITECOPY image page mapped r-x: %s, error %lu, %llu bytes\n",
           wrote ? "TRUE" : "FALSE", wrote ? 0 : GetLastError(), (unsigned long long)done);
    if (!ReadProcessMemory(process, (void *)&slot[1], &seen, sizeof(seen), &done))
    {
        printf("the page could not be read back\n");
        return finish(process, 1);
    }
    printf("the page holds %016llx after the write\n", seen);
    if (!wrote || seen != HOOKED)
    {
        printf("another process cannot write an image page that Wine reports as writable\n");
        return finish(process, 1);
    }
    printf("another process writes an image page that Wine reports as writable\n");
    return finish(process, 0);
}
