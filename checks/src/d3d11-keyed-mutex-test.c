#define COBJMACROS
#define WIDL_C_INLINE_WRAPPERS
#define INITGUID
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <d3d11_1.h>
#include <dxgi1_2.h>

#define WIDTH 256
#define HEIGHT 256
#define FRAMES 120
#define PRODUCER_DELAY_MS 300
#define SHORT_TIMEOUT_MS 50
#define MAX_GIVE_UP_MS 200

struct pair
{
    ID3D11Device *producer, *consumer;
    ID3D11DeviceContext *producer_context, *consumer_context;
    ID3D11Texture2D *shared, *opened, *staging;
    ID3D11RenderTargetView *rtv;
    IDXGIKeyedMutex *producer_mutex, *consumer_mutex;
};

static int fail(const char *step, HRESULT hr)
{
    printf("%s failed, hr=0x%08lx\n", step, (unsigned long)hr);
    fflush(stdout);
    return 1;
}

static HRESULT create_device(ID3D11Device **device, ID3D11DeviceContext **context)
{
    return D3D11CreateDevice(NULL, D3D_DRIVER_TYPE_HARDWARE, NULL, D3D11_CREATE_DEVICE_BGRA_SUPPORT, NULL, 0,
            D3D11_SDK_VERSION, device, NULL, context);
}

static int open_pair(struct pair *pair)
{
    ID3D11Device1 *consumer1;
    IDXGIResource1 *resource;
    D3D11_TEXTURE2D_DESC desc = { 0 };
    HANDLE handle;
    HRESULT hr;

    if (FAILED(hr = create_device(&pair->producer, &pair->producer_context)))
        return fail("D3D11CreateDevice for the producer", hr);
    if (FAILED(hr = create_device(&pair->consumer, &pair->consumer_context)))
        return fail("D3D11CreateDevice for the consumer", hr);

    desc.Width = WIDTH;
    desc.Height = HEIGHT;
    desc.MipLevels = 1;
    desc.ArraySize = 1;
    desc.Format = DXGI_FORMAT_B8G8R8A8_UNORM;
    desc.SampleDesc.Count = 1;
    desc.Usage = D3D11_USAGE_DEFAULT;
    desc.BindFlags = D3D11_BIND_RENDER_TARGET | D3D11_BIND_SHADER_RESOURCE;
    desc.MiscFlags = D3D11_RESOURCE_MISC_SHARED_NTHANDLE | D3D11_RESOURCE_MISC_SHARED_KEYEDMUTEX;
    if (FAILED(hr = ID3D11Device_CreateTexture2D(pair->producer, &desc, NULL, &pair->shared)))
        return fail("CreateTexture2D", hr);
    ID3D11Texture2D_QueryInterface(pair->shared, &IID_IDXGIResource1, (void **)&resource);
    if (FAILED(hr = IDXGIResource1_CreateSharedHandle(resource, NULL,
            DXGI_SHARED_RESOURCE_READ | DXGI_SHARED_RESOURCE_WRITE, NULL, &handle)))
        return fail("CreateSharedHandle", hr);
    ID3D11Texture2D_QueryInterface(pair->shared, &IID_IDXGIKeyedMutex, (void **)&pair->producer_mutex);
    ID3D11Device_CreateRenderTargetView(pair->producer, (ID3D11Resource *)pair->shared, NULL, &pair->rtv);

    ID3D11Device_QueryInterface(pair->consumer, &IID_ID3D11Device1, (void **)&consumer1);
    if (FAILED(hr = ID3D11Device1_OpenSharedResource1(consumer1, handle, &IID_ID3D11Texture2D, (void **)&pair->opened)))
        return fail("OpenSharedResource1", hr);
    ID3D11Texture2D_QueryInterface(pair->opened, &IID_IDXGIKeyedMutex, (void **)&pair->consumer_mutex);
    printf("texture with a keyed mutex created on one device and opened on a second one\n");
    fflush(stdout);

    desc.Usage = D3D11_USAGE_STAGING;
    desc.BindFlags = 0;
    desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
    desc.MiscFlags = 0;
    if (FAILED(hr = ID3D11Device_CreateTexture2D(pair->consumer, &desc, NULL, &pair->staging)))
        return fail("CreateTexture2D for reading", hr);
    return 0;
}

static int hand_over_frames(struct pair *pair)
{
    int frame;
    HRESULT hr;

    for (frame = 0; frame < FRAMES; frame++)
    {
        int odd = frame % 2;
        float color[4] = { odd ? 1.0f : 0.0f, 0.5f, odd ? 0.0f : 1.0f, 1.0f };
        BYTE expected_blue = odd ? 0 : 255;
        D3D11_MAPPED_SUBRESOURCE map;
        const BYTE *pixel;

        if ((hr = IDXGIKeyedMutex_AcquireSync(pair->producer_mutex, 0, 1000)) != S_OK)
            return fail("producer AcquireSync", hr);
        ID3D11DeviceContext_ClearRenderTargetView(pair->producer_context, pair->rtv, color);
        if (FAILED(hr = IDXGIKeyedMutex_ReleaseSync(pair->producer_mutex, 0)))
            return fail("producer ReleaseSync", hr);

        if ((hr = IDXGIKeyedMutex_AcquireSync(pair->consumer_mutex, 0, 1000)) != S_OK)
            return fail("consumer AcquireSync", hr);
        ID3D11DeviceContext_CopyResource(pair->consumer_context, (ID3D11Resource *)pair->staging,
                (ID3D11Resource *)pair->opened);
        if (FAILED(hr = IDXGIKeyedMutex_ReleaseSync(pair->consumer_mutex, 0)))
            return fail("consumer ReleaseSync", hr);

        if (FAILED(hr = ID3D11DeviceContext_Map(pair->consumer_context, (ID3D11Resource *)pair->staging, 0,
                D3D11_MAP_READ, 0, &map)))
            return fail("Map", hr);
        pixel = (const BYTE *)map.pData + (HEIGHT / 2) * map.RowPitch + (WIDTH / 2) * 4;
        if (abs((int)pixel[0] - expected_blue) > 1)
        {
            printf("frame %d: pixel (B G R A) %u %u %u %u, expected blue %u\n", frame,
                    pixel[0], pixel[1], pixel[2], pixel[3], expected_blue);
            ID3D11DeviceContext_Unmap(pair->consumer_context, (ID3D11Resource *)pair->staging, 0);
            return 1;
        }
        ID3D11DeviceContext_Unmap(pair->consumer_context, (ID3D11Resource *)pair->staging, 0);
    }
    printf("%d frames copied with the right colour\n", FRAMES);
    return 0;
}

static DWORD WINAPI delayed_release(void *argument)
{
    struct pair *pair = argument;
    float color[4] = { 1.0f, 0.5f, 0.0f, 1.0f };

    if (IDXGIKeyedMutex_AcquireSync(pair->producer_mutex, 0, 1000) != S_OK)
        return 1;
    Sleep(PRODUCER_DELAY_MS);
    ID3D11DeviceContext_ClearRenderTargetView(pair->producer_context, pair->rtv, color);
    return FAILED(IDXGIKeyedMutex_ReleaseSync(pair->producer_mutex, 1));
}

static int give_up_then_wait(struct pair *pair)
{
    HANDLE thread;
    DWORD start, waited;
    HRESULT hr;

    thread = CreateThread(NULL, 0, delayed_release, pair, 0, NULL);
    Sleep(50);
    start = GetTickCount();
    hr = IDXGIKeyedMutex_AcquireSync(pair->consumer_mutex, 1, SHORT_TIMEOUT_MS);
    waited = GetTickCount() - start;
    printf("consumer AcquireSync(1, %d ms): hr=0x%08lx after %lu ms, the producer holds the mutex for about %d ms\n",
            SHORT_TIMEOUT_MS, (unsigned long)hr, (unsigned long)waited, PRODUCER_DELAY_MS);
    if (hr != WAIT_TIMEOUT || waited >= MAX_GIVE_UP_MS || waited < SHORT_TIMEOUT_MS - 10)
    {
        printf("expected the wait to give up with WAIT_TIMEOUT after about %d ms\n", SHORT_TIMEOUT_MS);
        WaitForSingleObject(thread, INFINITE);
        return 1;
    }

    hr = IDXGIKeyedMutex_AcquireSync(pair->consumer_mutex, 1, INFINITE);
    WaitForSingleObject(thread, INFINITE);
    printf("consumer AcquireSync(1, INFINITE): hr=0x%08lx\n", (unsigned long)hr);
    if (hr != S_OK)
        return 1;
    IDXGIKeyedMutex_ReleaseSync(pair->consumer_mutex, 1);
    printf("the wait gave up on time and the next one waited for the producer\n");
    return 0;
}

int main(int argc, char **argv)
{
    struct pair pair = { 0 };

    if (open_pair(&pair))
        return 1;
    if (argc == 2 && !strcmp(argv[1], "timeout"))
        return give_up_then_wait(&pair);
    return hand_over_frames(&pair);
}
