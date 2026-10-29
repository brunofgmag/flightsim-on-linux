#define COBJMACROS
#define INITGUID
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <dxgi1_6.h>

static int fail(const char *step, HRESULT hr)
{
    printf("%s failed, hr=0x%08lx\n", step, (unsigned long)hr);
    fflush(stdout);
    return 1;
}

static const char *color_space_name(DXGI_COLOR_SPACE_TYPE space)
{
    switch (space)
    {
    case DXGI_COLOR_SPACE_RGB_FULL_G22_NONE_P709: return "SDR (G22_P709)";
    case DXGI_COLOR_SPACE_RGB_FULL_G10_NONE_P709: return "scRGB (G10_P709)";
    case DXGI_COLOR_SPACE_RGB_FULL_G2084_NONE_P2020: return "HDR10 (G2084_P2020)";
    default: return "another colour space";
    }
}

static int wine_reports_hdr(const WCHAR *device)
{
    UINT32 path_count = 0, mode_count = 0, i;
    DISPLAYCONFIG_PATH_INFO *paths;
    DISPLAYCONFIG_MODE_INFO *modes;
    int enabled = 0;

    if (GetDisplayConfigBufferSizes(QDC_ONLY_ACTIVE_PATHS, &path_count, &mode_count) != ERROR_SUCCESS)
        return 0;
    paths = calloc(path_count, sizeof(*paths));
    modes = calloc(mode_count, sizeof(*modes));
    if (QueryDisplayConfig(QDC_ONLY_ACTIVE_PATHS, &path_count, paths, &mode_count, modes, NULL) == ERROR_SUCCESS)
    {
        for (i = 0; i < path_count; i++)
        {
            DISPLAYCONFIG_SOURCE_DEVICE_NAME source = {0};
            DISPLAYCONFIG_GET_ADVANCED_COLOR_INFO color = {0};

            source.header.type = DISPLAYCONFIG_DEVICE_INFO_GET_SOURCE_NAME;
            source.header.size = sizeof(source);
            source.header.adapterId = paths[i].sourceInfo.adapterId;
            source.header.id = paths[i].sourceInfo.id;
            if (DisplayConfigGetDeviceInfo(&source.header) != ERROR_SUCCESS || lstrcmpW(source.viewGdiDeviceName, device))
                continue;
            color.header.type = DISPLAYCONFIG_DEVICE_INFO_GET_ADVANCED_COLOR_INFO;
            color.header.size = sizeof(color);
            color.header.adapterId = paths[i].targetInfo.adapterId;
            color.header.id = paths[i].targetInfo.id;
            if (DisplayConfigGetDeviceInfo(&color.header) == ERROR_SUCCESS && color.advancedColorEnabled)
                enabled = 1;
        }
    }
    free(paths);
    free(modes);
    return enabled;
}

int main(void)
{
    IDXGIFactory1 *factory;
    IDXGIAdapter1 *adapter;
    IDXGIOutput *output;
    unsigned int index, hdr_outputs = 0, lost = 0;
    HRESULT hr;

    if (FAILED(hr = CreateDXGIFactory1(&IID_IDXGIFactory1, (void **)&factory)))
        return fail("CreateDXGIFactory1", hr);
    if (FAILED(hr = IDXGIFactory1_EnumAdapters1(factory, 0, &adapter)))
        return fail("EnumAdapters1", hr);
    for (index = 0; IDXGIAdapter1_EnumOutputs(adapter, index, &output) == S_OK; index++)
    {
        IDXGIOutput6 *output6;
        DXGI_OUTPUT_DESC1 desc;
        int hdr;

        if (FAILED(hr = IDXGIOutput_QueryInterface(output, &IID_IDXGIOutput6, (void **)&output6)))
            return fail("QueryInterface(IDXGIOutput6)", hr);
        if (FAILED(hr = IDXGIOutput6_GetDesc1(output6, &desc)))
            return fail("GetDesc1", hr);
        hdr = wine_reports_hdr(desc.DeviceName);
        printf("output %u %ls: Wine says HDR %s, DXGI reports %s\n", index, desc.DeviceName, hdr ? "on" : "off",
                color_space_name(desc.ColorSpace));
        if (!hdr)
            continue;
        hdr_outputs++;
        if (desc.ColorSpace != DXGI_COLOR_SPACE_RGB_FULL_G2084_NONE_P2020)
            lost++;
    }
    fflush(stdout);
    if (!hdr_outputs)
        return 2;
    return lost ? 1 : 0;
}
