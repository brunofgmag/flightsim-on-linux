#include <windows.h>

static int guid_eq(REFIID a, const GUID *b)
{
    const unsigned char *pa = (const unsigned char *)a;
    const unsigned char *pb = (const unsigned char *)b;
    int i;
    for (i = 0; i < 16; i++)
        if (pa[i] != pb[i])
            return 0;
    return 1;
}

static const GUID IID_IUnknown_          = {0x00000000,0x0000,0x0000,{0xC0,0x00,0x00,0x00,0x00,0x00,0x00,0x46}};
static const GUID IID_IInspectable_      = {0xAF86E2E0,0xB12D,0x4C6A,{0x9C,0x5A,0xD7,0xAA,0x65,0x10,0x1E,0x90}};
static const GUID IID_IActivationFactory_= {0x00000035,0x0000,0x0000,{0xC0,0x00,0x00,0x00,0x00,0x00,0x00,0x46}};
static const GUID IID_IAgileObject_      = {0x94EA2B94,0xE9CC,0x49E0,{0xC0,0xFF,0xEE,0x64,0xCA,0x8F,0x5B,0x90}};
static const GUID IID_IToastMgrStatics_  = {0x50AC103F,0xD235,0x4598,{0xBB,0xEF,0x98,0xFE,0x4D,0x1A,0x3A,0xD4}};
static const GUID IID_IToastNotifier_    = {0x75927B93,0x03F3,0x41EC,{0x91,0xD3,0x6E,0x5B,0xAC,0x1B,0x38,0xE7}};

typedef void *HSTRING_;

typedef struct NotifierVtbl {
    HRESULT (STDMETHODCALLTYPE *QueryInterface)(void *This, REFIID riid, void **ppv);
    ULONG   (STDMETHODCALLTYPE *AddRef)(void *This);
    ULONG   (STDMETHODCALLTYPE *Release)(void *This);
    HRESULT (STDMETHODCALLTYPE *GetIids)(void *This, ULONG *c, GUID **iids);
    HRESULT (STDMETHODCALLTYPE *GetRuntimeClassName)(void *This, HSTRING_ *n);
    HRESULT (STDMETHODCALLTYPE *GetTrustLevel)(void *This, int *t);
    HRESULT (STDMETHODCALLTYPE *Show)(void *This, void *notif);
    HRESULT (STDMETHODCALLTYPE *Hide)(void *This, void *notif);
    HRESULT (STDMETHODCALLTYPE *get_Setting)(void *This, int *value);
    HRESULT (STDMETHODCALLTYPE *AddToSchedule)(void *This, void *s);
    HRESULT (STDMETHODCALLTYPE *RemoveFromSchedule)(void *This, void *s);
    HRESULT (STDMETHODCALLTYPE *GetScheduledToastNotifications)(void *This, void **result);
} NotifierVtbl;

typedef struct { const NotifierVtbl *vtbl; } Notifier;
static Notifier g_notifier;

static HRESULT STDMETHODCALLTYPE Notif_QI(void *This, REFIID riid, void **ppv)
{
    if (!ppv) return E_POINTER;
    if (guid_eq(riid, &IID_IUnknown_) || guid_eq(riid, &IID_IInspectable_) ||
        guid_eq(riid, &IID_IToastNotifier_) || guid_eq(riid, &IID_IAgileObject_)) {
        *ppv = &g_notifier;
        return S_OK;
    }
    *ppv = NULL;
    return E_NOINTERFACE;
}
static ULONG STDMETHODCALLTYPE Notif_AddRef(void *This)  { (void)This; return 2; }
static ULONG STDMETHODCALLTYPE Notif_Release(void *This) { (void)This; return 1; }
static HRESULT STDMETHODCALLTYPE Notif_GetIids(void *This, ULONG *c, GUID **iids) { (void)This; if(c)*c=0; if(iids)*iids=NULL; return S_OK; }
static HRESULT STDMETHODCALLTYPE Notif_GetRCN(void *This, HSTRING_ *n) { (void)This; if(n)*n=NULL; return S_OK; }
static HRESULT STDMETHODCALLTYPE Notif_GetTrust(void *This, int *t) { (void)This; if(t)*t=0; return S_OK; }
static HRESULT STDMETHODCALLTYPE Notif_Show(void *This, void *n) { (void)This; (void)n; return S_OK; }
static HRESULT STDMETHODCALLTYPE Notif_Hide(void *This, void *n) { (void)This; (void)n; return S_OK; }
static HRESULT STDMETHODCALLTYPE Notif_getSetting(void *This, int *value) { (void)This; if(value)*value=0; return S_OK; }
static HRESULT STDMETHODCALLTYPE Notif_Add(void *This, void *s) { (void)This; (void)s; return S_OK; }
static HRESULT STDMETHODCALLTYPE Notif_Rem(void *This, void *s) { (void)This; (void)s; return S_OK; }
static HRESULT STDMETHODCALLTYPE Notif_GetSched(void *This, void **r) { (void)This; if(r)*r=NULL; return E_NOTIMPL; }

static const NotifierVtbl g_notifierVtbl = {
    Notif_QI, Notif_AddRef, Notif_Release, Notif_GetIids, Notif_GetRCN, Notif_GetTrust,
    Notif_Show, Notif_Hide, Notif_getSetting, Notif_Add, Notif_Rem, Notif_GetSched
};

typedef struct AFVtbl {
    HRESULT (STDMETHODCALLTYPE *QueryInterface)(void *This, REFIID riid, void **ppv);
    ULONG   (STDMETHODCALLTYPE *AddRef)(void *This);
    ULONG   (STDMETHODCALLTYPE *Release)(void *This);
    HRESULT (STDMETHODCALLTYPE *GetIids)(void *This, ULONG *c, GUID **iids);
    HRESULT (STDMETHODCALLTYPE *GetRuntimeClassName)(void *This, HSTRING_ *n);
    HRESULT (STDMETHODCALLTYPE *GetTrustLevel)(void *This, int *t);
    HRESULT (STDMETHODCALLTYPE *ActivateInstance)(void *This, void **inst);
} AFVtbl;

typedef struct StaticsVtbl {
    HRESULT (STDMETHODCALLTYPE *QueryInterface)(void *This, REFIID riid, void **ppv);
    ULONG   (STDMETHODCALLTYPE *AddRef)(void *This);
    ULONG   (STDMETHODCALLTYPE *Release)(void *This);
    HRESULT (STDMETHODCALLTYPE *GetIids)(void *This, ULONG *c, GUID **iids);
    HRESULT (STDMETHODCALLTYPE *GetRuntimeClassName)(void *This, HSTRING_ *n);
    HRESULT (STDMETHODCALLTYPE *GetTrustLevel)(void *This, int *t);
    HRESULT (STDMETHODCALLTYPE *CreateToastNotifier)(void *This, void **result);
    HRESULT (STDMETHODCALLTYPE *CreateToastNotifierWithId)(void *This, HSTRING_ appId, void **result);
    HRESULT (STDMETHODCALLTYPE *GetTemplateContent)(void *This, int type, void **result);
} StaticsVtbl;

typedef struct {
    const AFVtbl      *vtblAF;
    const StaticsVtbl *vtblStatics;
} Factory;
static Factory g_factory;

static HRESULT STDMETHODCALLTYPE Fac_QI(void *This, REFIID riid, void **ppv)
{
    (void)This;
    if (!ppv) return E_POINTER;
    if (guid_eq(riid, &IID_IUnknown_) || guid_eq(riid, &IID_IInspectable_) ||
        guid_eq(riid, &IID_IActivationFactory_) || guid_eq(riid, &IID_IAgileObject_)) {
        *ppv = &g_factory;
        return S_OK;
    }
    if (guid_eq(riid, &IID_IToastMgrStatics_)) {
        *ppv = &g_factory.vtblStatics;
        return S_OK;
    }
    *ppv = NULL;
    return E_NOINTERFACE;
}
static ULONG STDMETHODCALLTYPE Fac_AddRef(void *This)  { (void)This; return 2; }
static ULONG STDMETHODCALLTYPE Fac_Release(void *This) { (void)This; return 1; }
static HRESULT STDMETHODCALLTYPE Fac_GetIids(void *This, ULONG *c, GUID **iids) { (void)This; if(c)*c=0; if(iids)*iids=NULL; return S_OK; }
static HRESULT STDMETHODCALLTYPE Fac_GetRCN(void *This, HSTRING_ *n) { (void)This; if(n)*n=NULL; return S_OK; }
static HRESULT STDMETHODCALLTYPE Fac_GetTrust(void *This, int *t) { (void)This; if(t)*t=0; return S_OK; }
static HRESULT STDMETHODCALLTYPE Fac_Activate(void *This, void **inst) { (void)This; if(inst)*inst=NULL; return E_NOTIMPL; }

static HRESULT STDMETHODCALLTYPE Stat_CreateNotifier(void *This, void **result)
{
    (void)This;
    if (!result) return E_POINTER;
    *result = &g_notifier;
    return S_OK;
}
static HRESULT STDMETHODCALLTYPE Stat_CreateNotifierWithId(void *This, HSTRING_ appId, void **result)
{
    (void)This; (void)appId;
    if (!result) return E_POINTER;
    *result = &g_notifier;
    return S_OK;
}
static HRESULT STDMETHODCALLTYPE Stat_GetTemplate(void *This, int type, void **result) { (void)This; (void)type; if(result)*result=NULL; return E_NOTIMPL; }

static const AFVtbl g_afVtbl = {
    Fac_QI, Fac_AddRef, Fac_Release, Fac_GetIids, Fac_GetRCN, Fac_GetTrust, Fac_Activate
};
static const StaticsVtbl g_staticsVtbl = {
    Fac_QI, Fac_AddRef, Fac_Release, Fac_GetIids, Fac_GetRCN, Fac_GetTrust,
    Stat_CreateNotifier, Stat_CreateNotifierWithId, Stat_GetTemplate
};

__declspec(dllexport) HRESULT STDMETHODCALLTYPE DllGetActivationFactory(HSTRING_ activatableClassId, void **factory)
{
    (void)activatableClassId;
    if (!factory) return E_POINTER;
    g_factory.vtblAF      = &g_afVtbl;
    g_factory.vtblStatics = &g_staticsVtbl;
    g_notifier.vtbl       = &g_notifierVtbl;
    *factory = &g_factory;
    return S_OK;
}

__declspec(dllexport) HRESULT STDMETHODCALLTYPE DllCanUnloadNow(void)
{
    return S_FALSE;
}

BOOL WINAPI DllMain(HINSTANCE h, DWORD reason, LPVOID reserved)
{
    (void)h; (void)reserved;
    if (reason == DLL_PROCESS_ATTACH) {
        g_factory.vtblAF      = &g_afVtbl;
        g_factory.vtblStatics = &g_staticsVtbl;
        g_notifier.vtbl       = &g_notifierVtbl;
    }
    return TRUE;
}
