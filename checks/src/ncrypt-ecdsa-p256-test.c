#include <windows.h>
#include <bcrypt.h>
#include <ncrypt.h>
#include <stdio.h>
#include <string.h>
#include <wchar.h>

#define PUBLIC_BLOB_SIZE (sizeof(BCRYPT_ECCKEY_BLOB) + 32 + 32)
#define HASH_SIZE 32
#define SIGNATURE_SIZE 64

static int fail_call(const char *call, SECURITY_STATUS status)
{
    printf("%s returned %#lx, Windows returns 0 FAIL\n", call, (unsigned long)status);
    return 1;
}

static int check_group(NCRYPT_KEY_HANDLE key, const char *which)
{
    WCHAR group[32] = {0};
    DWORD len = 0;
    SECURITY_STATUS status;

    status = NCryptGetProperty(key, NCRYPT_ALGORITHM_GROUP_PROPERTY, (PBYTE)group, sizeof(group), &len, 0);
    if (status) return fail_call("NCryptGetProperty \"Algorithm Group\"", status);
    if (wcscmp(group, L"ECDSA"))
    {
        printf("the Algorithm Group of the %s ECDSA_P256 key is \"%ls\", Windows returns \"ECDSA\" FAIL\n", which, group);
        return 1;
    }
    return 0;
}

static int create_signer(NCRYPT_PROV_HANDLE provider, NCRYPT_KEY_HANDLE *key, BYTE *blob)
{
    DWORD len = 0;
    SECURITY_STATUS status;

    status = NCryptCreatePersistedKey(provider, key, BCRYPT_ECDSA_P256_ALGORITHM, NULL, 0, 0);
    if (status) return fail_call("NCryptCreatePersistedKey for ECDSA_P256", status);
    status = NCryptFinalizeKey(*key, 0);
    if (status) return fail_call("NCryptFinalizeKey of an ECDSA_P256 key", status);
    if (check_group(*key, "created")) return 1;
    status = NCryptExportKey(*key, 0, BCRYPT_ECCPUBLIC_BLOB, NULL, blob, PUBLIC_BLOB_SIZE, &len, 0);
    if (status) return fail_call("NCryptExportKey of ECCPUBLICBLOB", status);
    if (len != PUBLIC_BLOB_SIZE || ((BCRYPT_ECCKEY_BLOB *)blob)->dwMagic != BCRYPT_ECDSA_PUBLIC_P256_MAGIC
            || ((BCRYPT_ECCKEY_BLOB *)blob)->cbKey != 32)
    {
        printf("the exported public blob has %lu bytes and magic %#lx, Windows returns %u bytes and magic %#x FAIL\n",
                len, (unsigned long)((BCRYPT_ECCKEY_BLOB *)blob)->dwMagic, (unsigned)PUBLIC_BLOB_SIZE,
                BCRYPT_ECDSA_PUBLIC_P256_MAGIC);
        return 1;
    }
    return 0;
}

int main(void)
{
    NCRYPT_PROV_HANDLE provider = 0;
    NCRYPT_KEY_HANDLE signer = 0, verifier = 0;
    BYTE blob[PUBLIC_BLOB_SIZE], hash[HASH_SIZE], signature[SIGNATURE_SIZE];
    DWORD len = 0, i;
    SECURITY_STATUS status;

    status = NCryptOpenStorageProvider(&provider, MS_KEY_STORAGE_PROVIDER, 0);
    if (status)
    {
        printf("NCryptOpenStorageProvider returned %#lx\n", (unsigned long)status);
        return 2;
    }
    if (create_signer(provider, &signer, blob)) return 1;

    status = NCryptImportKey(provider, 0, BCRYPT_ECCPUBLIC_BLOB, NULL, &verifier, blob, sizeof(blob), 0);
    if (status) return fail_call("NCryptImportKey of an ECDSA_P256 ECCPUBLICBLOB without parameters", status);
    if (check_group(verifier, "imported")) return 1;

    for (i = 0; i < HASH_SIZE; i++) hash[i] = (BYTE)(i * 7 + 1);
    status = NCryptSignHash(signer, NULL, hash, sizeof(hash), signature, sizeof(signature), &len, 0);
    if (status) return fail_call("NCryptSignHash with the ECDSA_P256 key", status);
    if (len != SIGNATURE_SIZE)
    {
        printf("NCryptSignHash gave a %lu byte signature, Windows gives %u FAIL\n", len, SIGNATURE_SIZE);
        return 1;
    }

    status = NCryptVerifySignature(verifier, NULL, hash, sizeof(hash), signature, sizeof(signature), 0);
    if (status) return fail_call("NCryptVerifySignature of a good signature with the imported key", status);

    signature[SIGNATURE_SIZE / 2] ^= 1;
    status = NCryptVerifySignature(verifier, NULL, hash, sizeof(hash), signature, sizeof(signature), 0);
    if (status != NTE_BAD_SIGNATURE)
    {
        printf("NCryptVerifySignature of a tampered signature returned %#lx, Windows returns %#lx FAIL\n",
                (unsigned long)status, (unsigned long)NTE_BAD_SIGNATURE);
        return 1;
    }
    printf("an imported ECDSA P-256 public key verified a %u byte signature and refused a tampered one ok\n",
            SIGNATURE_SIZE);

    NCryptFreeObject(verifier);
    NCryptFreeObject(signer);
    NCryptFreeObject(provider);
    return 0;
}
