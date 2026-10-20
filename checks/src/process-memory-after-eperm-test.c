#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define READS 2000
#define MARK 0x5741594c41544552ULL
#define SYS_PRCTL 157L
#define PR_SET_PTRACER 0x59616d61L
#define TEB_SYSCALL_DISPATCH 0x340
#define STALL_MICROSECONDS 2

static volatile ULONGLONG mark = MARK;
static volatile LONG stalls;

static long raw_prctl(long option, long arg)
{
    volatile unsigned char *selector = (volatile unsigned char *)NtCurrentTeb() + TEB_SYSCALL_DISPATCH;
    unsigned char saved = *selector;
    register long arg4 __asm__("r10") = 0;
    register long arg5 __asm__("r8") = 0;
    long ret;

    *selector = 0;
    __asm__ volatile ("syscall"
                      : "=a"(ret)
                      : "0"(SYS_PRCTL), "D"(option), "S"(arg), "d"(0L), "r"(arg4), "r"(arg5)
                      : "rcx", "r11", "memory");
    *selector = saved;
    return ret;
}

static void count_stalls(void)
{
    LARGE_INTEGER frequency, last, now;
    LONGLONG limit;

    QueryPerformanceFrequency(&frequency);
    limit = frequency.QuadPart * STALL_MICROSECONDS / 1000000;
    QueryPerformanceCounter(&last);
    for (;;)
    {
        QueryPerformanceCounter(&now);
        if (now.QuadPart - last.QuadPart > limit)
            stalls++;
        last = now;
    }
}

static int target(const char *mode, const char *event_text)
{
    HANDLE ready = (HANDLE)(ULONG_PTR)_strtoui64(event_text, NULL, 16);

    BOOL denied = !strcmp(mode, "denied");

    if (denied && raw_prctl(PR_SET_PTRACER, 0))
        return 3;
    SetEvent(ready);
    if (denied)
        Sleep(INFINITE);
    count_stalls();
    return 0;
}

static LONG read_stalls(HANDLE process)
{
    LONG value = -1;
    SIZE_T got;

    ReadProcessMemory(process, (const void *)&stalls, &value, sizeof(value), &got);
    return value;
}

static HANDLE start_target(const char *mode)
{
    SECURITY_ATTRIBUTES inherit = {sizeof(inherit), NULL, TRUE};
    STARTUPINFOA startup = {sizeof(startup)};
    PROCESS_INFORMATION info;
    char self[MAX_PATH], command[2 * MAX_PATH];
    HANDLE ready, waits[2];

    ready = CreateEventA(&inherit, TRUE, FALSE, NULL);
    GetModuleFileNameA(NULL, self, sizeof(self));
    snprintf(command, sizeof(command), "\"%s\" target %s %llx", self, mode, (unsigned long long)(ULONG_PTR)ready);
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

static double microseconds_per_read(HANDLE process, int *wrong)
{
    LARGE_INTEGER frequency, start, end;
    ULONGLONG value;
    SIZE_T got;
    int i;

    QueryPerformanceFrequency(&frequency);
    QueryPerformanceCounter(&start);
    for (i = 0; i < READS; i++)
    {
        value = 0;
        if (!ReadProcessMemory(process, (const void *)&mark, &value, sizeof(value), &got) || value != MARK)
            (*wrong)++;
    }
    QueryPerformanceCounter(&end);
    return (double)(end.QuadPart - start.QuadPart) * 1e6 / (double)frequency.QuadPart / READS;
}

static int finish(HANDLE free_target, HANDLE denied_target, int status)
{
    if (free_target) TerminateProcess(free_target, 0);
    if (denied_target) TerminateProcess(denied_target, 0);
    return status;
}

int main(int argc, char **argv)
{
    HANDLE free_target, denied_target;
    ULONGLONG value;
    SIZE_T got;
    double before, after;
    LONG stalls_start, stalls_before, stalls_middle, stalls_after;
    int wrong = 0;

    if (argc == 4 && !strcmp(argv[1], "target"))
        return target(argv[2], argv[3]);

    if (!(free_target = start_target("free")))
    {
        printf("the free target did not start\n");
        return 2;
    }
    if (!(denied_target = start_target("denied")))
    {
        printf("the denied target could not drop its own ptrace permission\n");
        return finish(free_target, NULL, 2);
    }

    stalls_start = read_stalls(free_target);
    before = microseconds_per_read(free_target, &wrong);
    stalls_before = read_stalls(free_target);
    if (ReadProcessMemory(denied_target, (const void *)&mark, &value, sizeof(value), &got))
    {
        printf("the denied target was read, so no EPERM happened\n");
        return finish(free_target, denied_target, 2);
    }
    stalls_middle = read_stalls(free_target);
    after = microseconds_per_read(free_target, &wrong);
    stalls_after = read_stalls(free_target);

    printf("before the EPERM: %.1f us per read, target main thread stopped %ld times in %d reads\n",
           before, stalls_before - stalls_start, READS);
    printf("after the EPERM: %.1f us per read, target main thread stopped %ld times in %d reads\n",
           after, stalls_after - stalls_middle, READS);
    if (wrong)
    {
        printf("%d reads of the free target failed or returned the wrong value\n", wrong);
        return finish(free_target, denied_target, 1);
    }
    if (stalls_after - stalls_middle > stalls_before - stalls_start + READS / 10)
    {
        printf("after an EPERM, reading another process stops its main thread\n");
        return finish(free_target, denied_target, 1);
    }
    printf("after an EPERM, reading another process still leaves its main thread running\n");
    return finish(free_target, denied_target, 0);
}
