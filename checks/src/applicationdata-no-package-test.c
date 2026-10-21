#define COBJMACROS
#define WIDL_C_INLINE_WRAPPERS
#define WIDL_using_Windows_Storage
#include <initguid.h>
#include <windows.h>
#include <stdio.h>
#include <roapi.h>
#include <winstring.h>
#include <windows.storage.h>

#define NO_PACKAGE_HRESULT ((HRESULT)0x80073d54)

int main(void)
{
    IApplicationDataStatics *statics;
    IApplicationData *data = NULL;
    HSTRING class_name;
    HRESULT hr;

    RoInitialize(RO_INIT_MULTITHREADED);
    if (FAILED(WindowsCreateString(RuntimeClass_Windows_Storage_ApplicationData,
                    ARRAYSIZE(RuntimeClass_Windows_Storage_ApplicationData) - 1, &class_name))
            || FAILED(RoGetActivationFactory(class_name, &IID_IApplicationDataStatics, (void **)&statics)))
    {
        printf("could not get the Windows.Storage.ApplicationData statics\n");
        return 2;
    }
    hr = IApplicationDataStatics_get_Current(statics, &data);
    if (hr != NO_PACKAGE_HRESULT || data)
    {
        printf("ApplicationData.Current in a process without a package returned %#lx and %s, Windows returns %#lx and no object FAIL\n",
                hr, data ? "an object" : "no object", NO_PACKAGE_HRESULT);
        return 1;
    }
    printf("ApplicationData.Current in a process without a package returned %#lx and no object ok\n", hr);
    IApplicationDataStatics_Release(statics);
    WindowsDeleteString(class_name);
    RoUninitialize();
    return 0;
}
