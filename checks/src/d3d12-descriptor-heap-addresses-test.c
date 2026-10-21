#define COBJMACROS
#define WIDL_C_INLINE_WRAPPERS
#define INITGUID
#include <windows.h>
#include <stdio.h>
#include <d3d12.h>

#define HEAPS_PER_CASE 8

static const UINT descriptor_counts[] = {65536, 100000, 1000000};
static const D3D12_DESCRIPTOR_HEAP_FLAGS heap_flags[] =
{
    D3D12_DESCRIPTOR_HEAP_FLAG_NONE,
    D3D12_DESCRIPTOR_HEAP_FLAG_SHADER_VISIBLE,
};

static BOOL is_readable_allocation_range(SIZE_T start, SIZE_T end)
{
    const DWORD readable = PAGE_READONLY | PAGE_READWRITE | PAGE_WRITECOPY |
            PAGE_EXECUTE_READ | PAGE_EXECUTE_READWRITE | PAGE_EXECUTE_WRITECOPY;
    MEMORY_BASIC_INFORMATION info;
    void *allocation_base = NULL;
    SIZE_T address = start;

    while (address < end)
    {
        if (!VirtualQuery((const void *)address, &info, sizeof(info)))
            return FALSE;
        if (info.State != MEM_COMMIT || !(info.Protect & readable) || (info.Protect & PAGE_GUARD))
            return FALSE;
        if (address != start && info.AllocationBase != allocation_base)
            return FALSE;
        allocation_base = info.AllocationBase;
        address = (SIZE_T)info.BaseAddress + info.RegionSize;
    }

    return TRUE;
}

int main(void)
{
    ID3D12DescriptorHeap *heaps[HEAPS_PER_CASE];
    unsigned int i, j, k, outside, failures = 0;
    D3D12_DESCRIPTOR_HEAP_DESC desc = {0};
    D3D12_CPU_DESCRIPTOR_HANDLE start;
    ID3D12Device *device;
    UINT increment;
    HRESULT hr;

    if (FAILED(hr = D3D12CreateDevice(NULL, D3D_FEATURE_LEVEL_11_0, &IID_ID3D12Device, (void **)&device)))
    {
        printf("D3D12CreateDevice failed, hr=0x%08lx\n", (unsigned long)hr);
        return 2;
    }

    increment = ID3D12Device_GetDescriptorHandleIncrementSize(device, D3D12_DESCRIPTOR_HEAP_TYPE_CBV_SRV_UAV);
    desc.Type = D3D12_DESCRIPTOR_HEAP_TYPE_CBV_SRV_UAV;

    for (i = 0; i < ARRAYSIZE(descriptor_counts); i++)
    {
        for (j = 0; j < ARRAYSIZE(heap_flags); j++)
        {
            desc.NumDescriptors = descriptor_counts[i];
            desc.Flags = heap_flags[j];

            for (k = 0; k < HEAPS_PER_CASE; k++)
            {
                if (FAILED(hr = ID3D12Device_CreateDescriptorHeap(device, &desc, &IID_ID3D12DescriptorHeap, (void **)&heaps[k])))
                {
                    printf("CreateDescriptorHeap with %u descriptors failed, hr=0x%08lx\n", desc.NumDescriptors, (unsigned long)hr);
                    return 2;
                }
            }

            outside = 0;
            for (k = 0; k < HEAPS_PER_CASE; k++)
            {
                start = ID3D12DescriptorHeap_GetCPUDescriptorHandleForHeapStart(heaps[k]);
                if (!is_readable_allocation_range(start.ptr, start.ptr + (SIZE_T)desc.NumDescriptors * increment))
                    outside++;
            }
            printf("%u descriptors, %s: %u of %u heaps hand out addresses outside their own allocation\n", desc.NumDescriptors,
                    desc.Flags ? "shader visible" : "CPU only", outside, HEAPS_PER_CASE);
            failures += outside;

            for (k = 0; k < HEAPS_PER_CASE; k++)
                ID3D12DescriptorHeap_Release(heaps[k]);
        }
    }

    ID3D12Device_Release(device);
    return failures ? 1 : 0;
}
