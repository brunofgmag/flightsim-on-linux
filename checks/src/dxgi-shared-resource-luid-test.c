#define COBJMACROS
#define WIDL_C_INLINE_WRAPPERS
#define INITGUID
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <d3d12.h>
#include <d3d11_1.h>
#include <dxgi1_4.h>

#define WIDTH 256
#define HEIGHT 256

static const float clear_color[4] = { 0.0f, 0.5f, 1.0f, 1.0f };

static int fail(const char *step, HRESULT hr)
{
    printf("%s failed, hr=0x%08lx\n", step, (unsigned long)hr);
    fflush(stdout);
    return 1;
}

static int close_enough(BYTE value, BYTE expected)
{
    return abs((int)value - (int)expected) <= 1;
}

static HRESULT duplicate_from_parent(DWORD parent_pid, HANDLE parent_handle, HANDLE *handle)
{
    HANDLE parent = OpenProcess(PROCESS_DUP_HANDLE, FALSE, parent_pid);

    if (!parent)
        return HRESULT_FROM_WIN32(GetLastError());
    if (!DuplicateHandle(parent, parent_handle, GetCurrentProcess(), handle, 0, FALSE, DUPLICATE_SAME_ACCESS))
        return HRESULT_FROM_WIN32(GetLastError());
    return S_OK;
}

static int same_luid(const LUID *a, const LUID *b)
{
    return a->LowPart == b->LowPart && a->HighPart == b->HighPart;
}

static int check_pixel(ID3D11Device *device, ID3D11DeviceContext *context, ID3D11Texture2D *shared)
{
    ID3D11Texture2D *staging;
    D3D11_TEXTURE2D_DESC desc;
    D3D11_MAPPED_SUBRESOURCE map;
    const BYTE *pixel;
    HRESULT hr;
    int matches;

    ID3D11Texture2D_GetDesc(shared, &desc);
    printf("opened texture: %ux%u, format %u, bind 0x%x, misc 0x%x\n",
            desc.Width, desc.Height, desc.Format, desc.BindFlags, desc.MiscFlags);

    desc.Usage = D3D11_USAGE_STAGING;
    desc.BindFlags = 0;
    desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
    desc.MiscFlags = 0;
    if (FAILED(hr = ID3D11Device_CreateTexture2D(device, &desc, NULL, &staging)))
        return fail("CreateTexture2D", hr);
    ID3D11DeviceContext_CopyResource(context, (ID3D11Resource *)staging, (ID3D11Resource *)shared);
    if (FAILED(hr = ID3D11DeviceContext_Map(context, (ID3D11Resource *)staging, 0, D3D11_MAP_READ, 0, &map)))
        return fail("Map", hr);
    pixel = (const BYTE *)map.pData + (HEIGHT / 2) * map.RowPitch + (WIDTH / 2) * 4;
    printf("middle pixel (B G R A): %u %u %u %u\n", pixel[0], pixel[1], pixel[2], pixel[3]);
    matches = close_enough(pixel[0], 255) && close_enough(pixel[1], 128) && close_enough(pixel[2], 0)
            && close_enough(pixel[3], 255);
    ID3D11DeviceContext_Unmap(context, (ID3D11Resource *)staging, 0);
    printf("pixel: %s\n", matches ? "the colour D3D12 cleared" : "another colour");
    return matches ? 0 : 1;
}

static int run_child(DWORD parent_pid, HANDLE parent_handle)
{
    IDXGIFactory2 *factory;
    ID3D11Device *device;
    ID3D11Device1 *device1;
    ID3D11DeviceContext *context;
    IDXGIDevice *dxgi_device;
    IDXGIAdapter *adapter;
    DXGI_ADAPTER_DESC adapter_desc;
    ID3D11Texture2D *shared;
    HANDLE handle, not_shared;
    LUID luid, other_luid;
    HRESULT hr;
    int same;

    if (FAILED(hr = duplicate_from_parent(parent_pid, parent_handle, &handle)))
        return fail("DuplicateHandle", hr);
    if (FAILED(hr = CreateDXGIFactory1(&IID_IDXGIFactory2, (void **)&factory)))
        return fail("CreateDXGIFactory1", hr);
    hr = IDXGIFactory2_GetSharedResourceAdapterLuid(factory, handle, &luid);
    printf("GetSharedResourceAdapterLuid: hr=0x%08lx\n", (unsigned long)hr);
    if (FAILED(hr))
        return 1;

    not_shared = CreateEventA(NULL, FALSE, FALSE, NULL);
    hr = IDXGIFactory2_GetSharedResourceAdapterLuid(factory, not_shared, &other_luid);
    printf("GetSharedResourceAdapterLuid on an event that is not a shared resource: hr=0x%08lx %s\n",
            (unsigned long)hr, FAILED(hr) ? "failed as it must" : "FAIL, it must fail");
    CloseHandle(not_shared);
    if (!FAILED(hr))
        return 1;

    if (FAILED(hr = D3D11CreateDevice(NULL, D3D_DRIVER_TYPE_HARDWARE, NULL, 0, NULL, 0,
            D3D11_SDK_VERSION, &device, NULL, &context)))
        return fail("D3D11CreateDevice", hr);
    ID3D11Device_QueryInterface(device, &IID_IDXGIDevice, (void **)&dxgi_device);
    IDXGIDevice_GetAdapter(dxgi_device, &adapter);
    IDXGIAdapter_GetDesc(adapter, &adapter_desc);
    same = same_luid(&luid, &adapter_desc.AdapterLuid);
    printf("resource LUID: %08lx:%08lx; D3D11 device LUID: %08lx:%08lx (%ls); same: %s\n",
            (unsigned long)luid.HighPart, (unsigned long)luid.LowPart,
            (unsigned long)adapter_desc.AdapterLuid.HighPart, (unsigned long)adapter_desc.AdapterLuid.LowPart,
            adapter_desc.Description, same ? "yes" : "no");
    if (!same)
        return 1;

    ID3D11Device_QueryInterface(device, &IID_ID3D11Device1, (void **)&device1);
    hr = ID3D11Device1_OpenSharedResource1(device1, handle, &IID_ID3D11Texture2D, (void **)&shared);
    printf("OpenSharedResource1: hr=0x%08lx\n", (unsigned long)hr);
    if (FAILED(hr))
        return 1;
    return check_pixel(device, context, shared);
}

static int clear_shared_texture(ID3D12Device *device, ID3D12Resource *texture)
{
    ID3D12CommandQueue *queue;
    ID3D12CommandAllocator *allocator;
    ID3D12GraphicsCommandList *list;
    ID3D12DescriptorHeap *rtv_heap;
    ID3D12Fence *fence;
    D3D12_COMMAND_QUEUE_DESC queue_desc = { 0 };
    D3D12_DESCRIPTOR_HEAP_DESC heap_desc = { 0 };
    D3D12_CPU_DESCRIPTOR_HANDLE rtv;
    HANDLE event;
    HRESULT hr;

    queue_desc.Type = D3D12_COMMAND_LIST_TYPE_DIRECT;
    if (FAILED(hr = ID3D12Device_CreateCommandQueue(device, &queue_desc, &IID_ID3D12CommandQueue, (void **)&queue)))
        return fail("CreateCommandQueue", hr);
    ID3D12Device_CreateCommandAllocator(device, D3D12_COMMAND_LIST_TYPE_DIRECT, &IID_ID3D12CommandAllocator, (void **)&allocator);
    ID3D12Device_CreateCommandList(device, 0, D3D12_COMMAND_LIST_TYPE_DIRECT, allocator, NULL,
            &IID_ID3D12GraphicsCommandList, (void **)&list);

    heap_desc.Type = D3D12_DESCRIPTOR_HEAP_TYPE_RTV;
    heap_desc.NumDescriptors = 1;
    ID3D12Device_CreateDescriptorHeap(device, &heap_desc, &IID_ID3D12DescriptorHeap, (void **)&rtv_heap);
    rtv = ID3D12DescriptorHeap_GetCPUDescriptorHandleForHeapStart(rtv_heap);
    ID3D12Device_CreateRenderTargetView(device, texture, NULL, rtv);
    ID3D12GraphicsCommandList_ClearRenderTargetView(list, rtv, clear_color, 0, NULL);
    ID3D12GraphicsCommandList_Close(list);
    ID3D12CommandQueue_ExecuteCommandLists(queue, 1, (ID3D12CommandList **)&list);

    ID3D12Device_CreateFence(device, 0, D3D12_FENCE_FLAG_NONE, &IID_ID3D12Fence, (void **)&fence);
    event = CreateEventA(NULL, FALSE, FALSE, NULL);
    ID3D12CommandQueue_Signal(queue, fence, 1);
    ID3D12Fence_SetEventOnCompletion(fence, 1, event);
    WaitForSingleObject(event, INFINITE);
    return 0;
}

static int create_shared_texture(ID3D12Device *device, ID3D12Resource **texture)
{
    D3D12_HEAP_PROPERTIES heap_props = { 0 };
    D3D12_RESOURCE_DESC desc = { 0 };
    HRESULT hr;

    heap_props.Type = D3D12_HEAP_TYPE_DEFAULT;
    desc.Dimension = D3D12_RESOURCE_DIMENSION_TEXTURE2D;
    desc.Width = WIDTH;
    desc.Height = HEIGHT;
    desc.DepthOrArraySize = 1;
    desc.MipLevels = 1;
    desc.Format = DXGI_FORMAT_B8G8R8A8_UNORM;
    desc.SampleDesc.Count = 1;
    desc.Flags = D3D12_RESOURCE_FLAG_ALLOW_RENDER_TARGET | D3D12_RESOURCE_FLAG_ALLOW_SIMULTANEOUS_ACCESS;
    if (FAILED(hr = ID3D12Device_CreateCommittedResource(device, &heap_props, D3D12_HEAP_FLAG_SHARED, &desc,
            D3D12_RESOURCE_STATE_RENDER_TARGET, NULL, &IID_ID3D12Resource, (void **)texture)))
        return fail("CreateCommittedResource", hr);
    return 0;
}

static int run_producer(const char *self)
{
    ID3D12Device *device;
    ID3D12Resource *texture;
    PROCESS_INFORMATION pi;
    STARTUPINFOA si = { sizeof(si) };
    HANDLE shared;
    char cmdline[MAX_PATH + 64];
    DWORD exit_code;
    HRESULT hr;

    if (FAILED(hr = D3D12CreateDevice(NULL, D3D_FEATURE_LEVEL_11_0, &IID_ID3D12Device, (void **)&device)))
        return fail("D3D12CreateDevice", hr);
    if (create_shared_texture(device, &texture) || clear_shared_texture(device, texture))
        return 1;
    if (FAILED(hr = ID3D12Device_CreateSharedHandle(device, (ID3D12DeviceChild *)texture, NULL, GENERIC_ALL, NULL, &shared)))
        return fail("CreateSharedHandle", hr);
    printf("D3D12: %ux%u texture shared with a handle, handle %p\n", WIDTH, HEIGHT, shared);
    fflush(stdout);

    snprintf(cmdline, sizeof(cmdline), "\"%s\" child %lu %llu", self, GetCurrentProcessId(),
            (unsigned long long)(UINT_PTR)shared);
    if (!CreateProcessA(NULL, cmdline, NULL, NULL, FALSE, 0, NULL, NULL, &si, &pi))
        return fail("CreateProcess", HRESULT_FROM_WIN32(GetLastError()));
    WaitForSingleObject(pi.hProcess, INFINITE);
    GetExitCodeProcess(pi.hProcess, &exit_code);
    printf("child process exited with %lu\n", exit_code);
    return exit_code ? 1 : 0;
}

int main(int argc, char **argv)
{
    char self[MAX_PATH];

    if (argc == 4 && !strcmp(argv[1], "child"))
        return run_child(strtoul(argv[2], NULL, 10), (HANDLE)(UINT_PTR)_strtoui64(argv[3], NULL, 10));

    GetModuleFileNameA(NULL, self, sizeof(self));
    return run_producer(self);
}
