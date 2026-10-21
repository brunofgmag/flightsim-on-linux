#include <windows.h>
#include <bcrypt.h>
#include <ncrypt.h>
#include <stdio.h>
#include <string.h>
#include <wchar.h>

#ifndef NCRYPTBUFFER_ECC_CURVE_NAME
#define NCRYPTBUFFER_ECC_CURVE_NAME 60
#endif

#define PUBLIC_BLOB_SIZE (sizeof(BCRYPT_ECCKEY_BLOB) + 32 + 32)
#define SECRET_SIZE 32

typedef SECURITY_STATUS (WINAPI *secret_agreement_func)(NCRYPT_KEY_HANDLE, NCRYPT_KEY_HANDLE,
        NCRYPT_SECRET_HANDLE *, DWORD);
typedef SECURITY_STATUS (WINAPI *derive_key_func)(NCRYPT_SECRET_HANDLE, LPCWSTR, NCryptBufferDesc *,
        PBYTE, DWORD, DWORD *, ULONG);

struct peer
{
    NCRYPT_KEY_HANDLE key;
    BYTE blob[PUBLIC_BLOB_SIZE];
    NCRYPT_KEY_HANDLE remote;
    NCRYPT_SECRET_HANDLE secret;
    BYTE raw[SECRET_SIZE];
    BYTE hashed[SECRET_SIZE];
};

static int fail_call(const char *call, SECURITY_STATUS status)
{
    printf("%s returned %#lx, Windows returns 0 FAIL\n", call, (unsigned long)status);
    return 1;
}

static int create_peer(NCRYPT_PROV_HANDLE provider, struct peer *peer)
{
    WCHAR group[32] = {0};
    DWORD len = 0;
    SECURITY_STATUS status;

    status = NCryptCreatePersistedKey(provider, &peer->key, BCRYPT_ECDH_P256_ALGORITHM, NULL, 0, 0);
    if (status) return fail_call("NCryptCreatePersistedKey for ECDH_P256", status);
    status = NCryptFinalizeKey(peer->key, 0);
    if (status) return fail_call("NCryptFinalizeKey of an ECDH_P256 key", status);
    status = NCryptGetProperty(peer->key, NCRYPT_ALGORITHM_GROUP_PROPERTY, (PBYTE)group, sizeof(group), &len, 0);
    if (status) return fail_call("NCryptGetProperty \"Algorithm Group\"", status);
    if (wcscmp(group, L"ECDH"))
    {
        printf("the Algorithm Group of an ECDH_P256 key is \"%ls\", Windows returns \"ECDH\" FAIL\n", group);
        return 1;
    }
    status = NCryptExportKey(peer->key, 0, BCRYPT_ECCPUBLIC_BLOB, NULL, peer->blob, sizeof(peer->blob), &len, 0);
    if (status) return fail_call("NCryptExportKey of ECCPUBLICBLOB", status);
    if (len != PUBLIC_BLOB_SIZE || ((BCRYPT_ECCKEY_BLOB *)peer->blob)->dwMagic != BCRYPT_ECDH_PUBLIC_P256_MAGIC
            || ((BCRYPT_ECCKEY_BLOB *)peer->blob)->cbKey != 32)
    {
        printf("the exported public blob has %lu bytes and magic %#lx, Windows returns %u bytes and magic %#x FAIL\n",
                len, (unsigned long)((BCRYPT_ECCKEY_BLOB *)peer->blob)->dwMagic, (unsigned)PUBLIC_BLOB_SIZE,
                BCRYPT_ECDH_PUBLIC_P256_MAGIC);
        return 1;
    }
    return 0;
}

static int import_public(NCRYPT_PROV_HANDLE provider, const BYTE *blob, NCRYPT_KEY_HANDLE *imported)
{
    NCryptBuffer buffer;
    NCryptBufferDesc desc;
    SECURITY_STATUS status;

    buffer.cbBuffer = sizeof(L"nistP256");
    buffer.BufferType = NCRYPTBUFFER_ECC_CURVE_NAME;
    buffer.pvBuffer = (void *)L"nistP256";
    desc.ulVersion = NCRYPTBUFFER_VERSION;
    desc.cBuffers = 1;
    desc.pBuffers = &buffer;
    status = NCryptImportKey(provider, 0, BCRYPT_ECCPUBLIC_BLOB, &desc, imported, (PBYTE)blob, PUBLIC_BLOB_SIZE, 0);
    if (status) return fail_call("NCryptImportKey of a peer ECCPUBLICBLOB with the nistP256 curve name", status);
    return 0;
}

static int agree(secret_agreement_func secret_agreement, struct peer *peer)
{
    SECURITY_STATUS status = secret_agreement(peer->key, peer->remote, &peer->secret, 0);
    if (status) return fail_call("NCryptSecretAgreement", status);
    return 0;
}

static int derive(derive_key_func derive_key, struct peer *peer)
{
    NCryptBuffer buffer;
    NCryptBufferDesc desc;
    DWORD len = 0;
    SECURITY_STATUS status;

    status = derive_key(peer->secret, BCRYPT_KDF_RAW_SECRET, NULL, peer->raw, sizeof(peer->raw), &len, 0);
    if (status) return fail_call("NCryptDeriveKey with TRUNCATE", status);
    if (len != SECRET_SIZE)
    {
        printf("NCryptDeriveKey with TRUNCATE gave %lu bytes, Windows gives %u FAIL\n", len, SECRET_SIZE);
        return 1;
    }
    buffer.cbBuffer = sizeof(L"SHA256");
    buffer.BufferType = KDF_HASH_ALGORITHM;
    buffer.pvBuffer = (void *)L"SHA256";
    desc.ulVersion = NCRYPTBUFFER_VERSION;
    desc.cBuffers = 1;
    desc.pBuffers = &buffer;
    status = derive_key(peer->secret, BCRYPT_KDF_HASH, &desc, peer->hashed, sizeof(peer->hashed), &len, 0);
    if (status) return fail_call("NCryptDeriveKey with HASH and SHA256", status);
    if (len != SECRET_SIZE)
    {
        printf("NCryptDeriveKey with HASH and SHA256 gave %lu bytes, Windows gives %u FAIL\n", len, SECRET_SIZE);
        return 1;
    }
    return 0;
}

static int all_zero(const BYTE *data, size_t size)
{
    size_t i;
    for (i = 0; i < size; i++) if (data[i]) return 0;
    return 1;
}

int main(void)
{
    struct peer alice = {0}, bob = {0};
    NCRYPT_PROV_HANDLE provider = 0;
    secret_agreement_func secret_agreement;
    derive_key_func derive_key;
    HMODULE ncrypt;
    SECURITY_STATUS status;

    status = NCryptOpenStorageProvider(&provider, MS_KEY_STORAGE_PROVIDER, 0);
    if (status)
    {
        printf("NCryptOpenStorageProvider returned %#lx\n", (unsigned long)status);
        return 2;
    }
    if (create_peer(provider, &alice) || create_peer(provider, &bob)) return 1;

    ncrypt = GetModuleHandleW(L"ncrypt.dll");
    secret_agreement = ncrypt ? (secret_agreement_func)(void *)GetProcAddress(ncrypt, "NCryptSecretAgreement") : NULL;
    derive_key = ncrypt ? (derive_key_func)(void *)GetProcAddress(ncrypt, "NCryptDeriveKey") : NULL;
    if (!secret_agreement || !derive_key)
    {
        printf("ncrypt.dll does not export NCryptSecretAgreement and NCryptDeriveKey FAIL\n");
        return 1;
    }

    if (import_public(provider, bob.blob, &alice.remote) || import_public(provider, alice.blob, &bob.remote)) return 1;
    if (agree(secret_agreement, &alice) || agree(secret_agreement, &bob)) return 1;
    if (derive(derive_key, &alice) || derive(derive_key, &bob)) return 1;

    if (memcmp(alice.raw, bob.raw, SECRET_SIZE) || all_zero(alice.raw, SECRET_SIZE))
    {
        printf("the two ECDH P-256 sides derived %s raw secrets with TRUNCATE, Windows derives 32 equal, non-zero bytes FAIL\n",
                all_zero(alice.raw, SECRET_SIZE) ? "zero" : "different");
        return 1;
    }
    if (memcmp(alice.hashed, bob.hashed, SECRET_SIZE) || all_zero(alice.hashed, SECRET_SIZE))
    {
        printf("the two ECDH P-256 sides derived different keys with HASH and SHA256, Windows derives 32 equal bytes FAIL\n");
        return 1;
    }
    printf("two ECDH P-256 keys agreed on a %u byte secret with TRUNCATE and on a %u byte SHA256 key with HASH ok\n",
            SECRET_SIZE, SECRET_SIZE);

    NCryptFreeObject(alice.secret);
    NCryptFreeObject(bob.secret);
    NCryptFreeObject(alice.remote);
    NCryptFreeObject(bob.remote);
    NCryptFreeObject(alice.key);
    NCryptFreeObject(bob.key);
    NCryptFreeObject(provider);
    return 0;
}
