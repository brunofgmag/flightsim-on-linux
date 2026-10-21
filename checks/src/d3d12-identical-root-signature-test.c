#define COBJMACROS
#define WIDL_C_INLINE_WRAPPERS
#define INITGUID
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <d3d12.h>

static int failures;

static int step_failed(const char *step, HRESULT hr)
{
    printf("%s failed, hr=0x%08lx\n", step, (unsigned long)hr);
    return 2;
}

static void expect(BOOL condition, const char *what)
{
    printf("%s: %s\n", what, condition ? "ok" : "FAILED");
    if (!condition)
        failures++;
}

static ULONG references(ID3D12RootSignature *root_signature)
{
    ID3D12RootSignature_AddRef(root_signature);
    return ID3D12RootSignature_Release(root_signature);
}

static HRESULT serialize(UINT constants, ID3DBlob **blob)
{
    D3D12_ROOT_PARAMETER parameter = {0};
    D3D12_ROOT_SIGNATURE_DESC desc = {0};

    parameter.ParameterType = D3D12_ROOT_PARAMETER_TYPE_32BIT_CONSTANTS;
    parameter.Constants.Num32BitValues = constants;
    parameter.ShaderVisibility = D3D12_SHADER_VISIBILITY_ALL;
    desc.NumParameters = 1;
    desc.pParameters = &parameter;
    return D3D12SerializeRootSignature(&desc, D3D_ROOT_SIGNATURE_VERSION_1_0, blob, NULL);
}

static HRESULT create(ID3D12Device *device, const void *bytes, SIZE_T size, ID3D12RootSignature **root_signature)
{
    return ID3D12Device_CreateRootSignature(device, 0, bytes, size, &IID_ID3D12RootSignature, (void **)root_signature);
}

int main(void)
{
    ID3D12RootSignature *first, *second, *from_copy, *other, *after_release;
    ID3DBlob *blob, *other_blob;
    ID3D12Device *device;
    const void *bytes;
    void *copy;
    SIZE_T size;
    HRESULT hr;

    if (FAILED(hr = D3D12CreateDevice(NULL, D3D_FEATURE_LEVEL_11_0, &IID_ID3D12Device, (void **)&device)))
        return step_failed("D3D12CreateDevice", hr);
    if (FAILED(hr = serialize(4, &blob)) || FAILED(hr = serialize(8, &other_blob)))
        return step_failed("D3D12SerializeRootSignature", hr);
    bytes = ID3D10Blob_GetBufferPointer(blob);
    size = ID3D10Blob_GetBufferSize(blob);
    copy = malloc(size);
    memcpy(copy, bytes, size);

    if (FAILED(hr = create(device, bytes, size, &first)) || FAILED(hr = create(device, bytes, size, &second))
            || FAILED(hr = create(device, copy, size, &from_copy))
            || FAILED(hr = create(device, ID3D10Blob_GetBufferPointer(other_blob), ID3D10Blob_GetBufferSize(other_blob), &other)))
        return step_failed("CreateRootSignature", hr);

    expect(second == first, "the same blob twice gives the same object");
    expect(from_copy == first, "a copy of the blob at another address gives the same object");
    expect(other != first, "a different blob gives another object");
    ID3D12RootSignature_Release(from_copy);
    ID3D12RootSignature_Release(second);
    expect(references(first) == 1, "the object keeps one reference once the extra ones are released");
    ID3D12RootSignature_Release(other);
    expect(ID3D12RootSignature_Release(first) == 0, "the last release frees the object");

    if (FAILED(hr = create(device, bytes, size, &after_release)))
        return step_failed("CreateRootSignature after the release", hr);
    expect(references(after_release) == 1, "the blob after the object was freed gives a new object with one reference");
    ID3D12RootSignature_Release(after_release);

    free(copy);
    ID3D10Blob_Release(other_blob);
    ID3D10Blob_Release(blob);
    ID3D12Device_Release(device);
    return failures ? 1 : 0;
}
