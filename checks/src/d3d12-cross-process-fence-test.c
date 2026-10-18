#define COBJMACROS
#define WIDL_C_INLINE_WRAPPERS
#define INITGUID
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <d3d12.h>
#include <d3d11_4.h>

#define READY_EVENT "Local\\d3d12-cross-process-fence-ready"
#define ACK_EVENT "Local\\d3d12-cross-process-fence-received"
#define TIMEOUT_MS 5000
#define FIRST_VALUE 10
#define PARENT_VALUE 25
#define CHILD_VALUE 40

static void say(const char *who, const char *text)
{
    printf("%s: %s\n", who, text);
    fflush(stdout);
}

static int step_failed(const char *who, const char *step, HRESULT hr)
{
    printf("%s: %s failed, hr=0x%08lx\n", who, step, (unsigned long)hr);
    fflush(stdout);
    return 2;
}

static int run_child(DWORD parent_pid, HANDLE parent_handle)
{
    HANDLE parent, handle, ready, ack, reached;
    ID3D11Device *device;
    ID3D11Device5 *device5;
    ID3D11DeviceContext *context;
    ID3D11DeviceContext4 *context4;
    ID3D11Fence *fence;
    UINT64 value;
    HRESULT hr;

    if (!(parent = OpenProcess(PROCESS_DUP_HANDLE, FALSE, parent_pid)))
        return step_failed("child", "OpenProcess", HRESULT_FROM_WIN32(GetLastError()));
    if (!DuplicateHandle(parent, parent_handle, GetCurrentProcess(), &handle, 0, FALSE, DUPLICATE_SAME_ACCESS))
        return step_failed("child", "DuplicateHandle", HRESULT_FROM_WIN32(GetLastError()));
    if (!(ready = OpenEventA(EVENT_MODIFY_STATE, FALSE, READY_EVENT)))
        return step_failed("child", "OpenEvent ready", HRESULT_FROM_WIN32(GetLastError()));
    if (!(ack = OpenEventA(SYNCHRONIZE, FALSE, ACK_EVENT)))
        return step_failed("child", "OpenEvent received", HRESULT_FROM_WIN32(GetLastError()));

    if (FAILED(hr = D3D11CreateDevice(NULL, D3D_DRIVER_TYPE_HARDWARE, NULL, 0, NULL, 0,
            D3D11_SDK_VERSION, &device, NULL, &context)))
        return step_failed("child", "D3D11CreateDevice", hr);
    if (FAILED(hr = ID3D11Device_QueryInterface(device, &IID_ID3D11Device5, (void **)&device5)))
        return step_failed("child", "QueryInterface ID3D11Device5", hr);
    if (FAILED(hr = ID3D11DeviceContext_QueryInterface(context, &IID_ID3D11DeviceContext4, (void **)&context4)))
        return step_failed("child", "QueryInterface ID3D11DeviceContext4", hr);

    say("child", "opening the parent's D3D12 fence with OpenSharedFence");
    if (FAILED(hr = ID3D11Device5_OpenSharedFence(device5, handle, &IID_ID3D11Fence, (void **)&fence)))
        return step_failed("child", "OpenSharedFence", hr);
    say("child", "OpenSharedFence worked");

    value = ID3D11Fence_GetCompletedValue(fence);
    printf("child: fence value on opening %llu, expected %u\n", (unsigned long long)value, FIRST_VALUE);
    fflush(stdout);
    if (value != FIRST_VALUE)
        return 1;

    reached = CreateEventA(NULL, FALSE, FALSE, NULL);
    if (FAILED(hr = ID3D11Fence_SetEventOnCompletion(fence, PARENT_VALUE, reached)))
        return step_failed("child", "SetEventOnCompletion for the parent's value", hr);
    SetEvent(ready);
    if (WaitForSingleObject(reached, TIMEOUT_MS) != WAIT_OBJECT_0)
    {
        printf("child: the value %u signalled by the parent did not arrive in %u ms (current value %llu)\n",
                PARENT_VALUE, TIMEOUT_MS, (unsigned long long)ID3D11Fence_GetCompletedValue(fence));
        fflush(stdout);
        return 1;
    }
    value = ID3D11Fence_GetCompletedValue(fence);
    printf("child: the parent's value arrived, fence at %llu, expected %u\n", (unsigned long long)value, PARENT_VALUE);
    fflush(stdout);
    if (value != PARENT_VALUE)
        return 1;

    if (FAILED(hr = ID3D11DeviceContext4_Signal(context4, fence, CHILD_VALUE)))
        return step_failed("child", "Signal for the child's value", hr);
    ID3D11DeviceContext_Flush(context);
    printf("child: signalled %u\n", CHILD_VALUE);
    fflush(stdout);
    if (FAILED(hr = ID3D11Fence_SetEventOnCompletion(fence, CHILD_VALUE, reached)))
        return step_failed("child", "SetEventOnCompletion for its own value", hr);
    if (WaitForSingleObject(reached, TIMEOUT_MS) != WAIT_OBJECT_0)
    {
        printf("child: its own signal %u did not complete in %u ms\n", CHILD_VALUE, TIMEOUT_MS);
        fflush(stdout);
        return 1;
    }
    say("child", "its own signal completed; waiting for the parent to confirm");
    if (WaitForSingleObject(ack, TIMEOUT_MS) != WAIT_OBJECT_0)
        say("child", "the parent did not confirm");
    return 0;
}

static int run_parent(const char *self)
{
    ID3D12Device *device;
    ID3D12CommandQueue *queue;
    ID3D12Fence *fence;
    D3D12_COMMAND_QUEUE_DESC queue_desc = { 0 };
    PROCESS_INFORMATION pi;
    STARTUPINFOA si = { sizeof(si) };
    HANDLE shared, ready, ack, reached, waits[2];
    char cmdline[MAX_PATH + 64];
    DWORD exit_code, which;
    UINT64 value;
    HRESULT hr;

    if (FAILED(hr = D3D12CreateDevice(NULL, D3D_FEATURE_LEVEL_11_0, &IID_ID3D12Device, (void **)&device)))
        return step_failed("parent", "D3D12CreateDevice", hr);
    queue_desc.Type = D3D12_COMMAND_LIST_TYPE_DIRECT;
    if (FAILED(hr = ID3D12Device_CreateCommandQueue(device, &queue_desc, &IID_ID3D12CommandQueue, (void **)&queue)))
        return step_failed("parent", "CreateCommandQueue", hr);
    if (FAILED(hr = ID3D12Device_CreateFence(device, 0, D3D12_FENCE_FLAG_SHARED, &IID_ID3D12Fence, (void **)&fence)))
        return step_failed("parent", "CreateFence shared", hr);

    reached = CreateEventA(NULL, FALSE, FALSE, NULL);
    ID3D12CommandQueue_Signal(queue, fence, FIRST_VALUE);
    ID3D12Fence_SetEventOnCompletion(fence, FIRST_VALUE, reached);
    if (WaitForSingleObject(reached, TIMEOUT_MS) != WAIT_OBJECT_0)
        return step_failed("parent", "waiting for the first value in its own process", E_FAIL);

    if (FAILED(hr = ID3D12Device_CreateSharedHandle(device, (ID3D12DeviceChild *)fence, NULL, GENERIC_ALL, NULL, &shared)))
        return step_failed("parent", "CreateSharedHandle", hr);
    printf("parent: shared D3D12 fence at value %u, handle %p\n", FIRST_VALUE, shared);
    fflush(stdout);

    ready = CreateEventA(NULL, FALSE, FALSE, READY_EVENT);
    ack = CreateEventA(NULL, TRUE, FALSE, ACK_EVENT);
    snprintf(cmdline, sizeof(cmdline), "\"%s\" child %lu %llu", self, GetCurrentProcessId(),
            (unsigned long long)(UINT_PTR)shared);
    if (!CreateProcessA(NULL, cmdline, NULL, NULL, FALSE, 0, NULL, NULL, &si, &pi))
        return step_failed("parent", "CreateProcess", HRESULT_FROM_WIN32(GetLastError()));

    waits[0] = ready;
    waits[1] = pi.hProcess;
    which = WaitForMultipleObjects(2, waits, FALSE, 3 * TIMEOUT_MS);
    if (which == WAIT_OBJECT_0)
    {
        ID3D12CommandQueue_Signal(queue, fence, PARENT_VALUE);
        printf("parent: signalled %u\n", PARENT_VALUE);
        fflush(stdout);
        ID3D12Fence_SetEventOnCompletion(fence, CHILD_VALUE, reached);
        waits[0] = reached;
        which = WaitForMultipleObjects(2, waits, FALSE, TIMEOUT_MS);
        if (which == WAIT_OBJECT_0)
            SetEvent(ack);
    }

    if (WaitForSingleObject(pi.hProcess, 3 * TIMEOUT_MS) != WAIT_OBJECT_0)
    {
        say("parent", "the child did not exit; ending it");
        TerminateProcess(pi.hProcess, 99);
        WaitForSingleObject(pi.hProcess, INFINITE);
    }
    GetExitCodeProcess(pi.hProcess, &exit_code);
    value = ID3D12Fence_GetCompletedValue(fence);
    printf("parent: fence at %llu, expected %u %s\n", (unsigned long long)value, CHILD_VALUE,
            value == CHILD_VALUE ? "ok" : "FAIL");
    printf("parent: child exit code 0x%lx %s\n", exit_code, exit_code ? "FAIL" : "ok");
    fflush(stdout);
    return 0;
}

int main(int argc, char **argv)
{
    char self[MAX_PATH];

    if (argc == 4 && !strcmp(argv[1], "child"))
    {
        run_child(strtoul(argv[2], NULL, 10), (HANDLE)(UINT_PTR)_strtoui64(argv[3], NULL, 10));
        return 0;
    }

    GetModuleFileNameA(NULL, self, sizeof(self));
    return run_parent(self);
}
