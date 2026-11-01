#include <windows.h>
#include <bcrypt.h>
#include <stdio.h>
#include <string.h>

#ifndef BCRYPT_PBKDF2_ALG_HANDLE
#define BCRYPT_PBKDF2_ALG_HANDLE ((BCRYPT_ALG_HANDLE)0x00000331)
#endif

static const unsigned char expected[32] =
{
    0x12, 0x0f, 0xb6, 0xcf, 0xfc, 0xf8, 0xb3, 0x2c, 0x43, 0xe7, 0x22, 0x52, 0x56, 0xc4, 0xf8, 0x37,
    0xa8, 0x65, 0x48, 0xc9, 0x2c, 0xcc, 0x35, 0x48, 0x08, 0x05, 0x98, 0x7c, 0xb7, 0x0b, 0xe1, 0x7b,
};

static void print_hex(const unsigned char *data, ULONG size)
{
    ULONG i;
    for (i = 0; i < size; i++) printf("%02x", data[i]);
}

int main(void)
{
    static const unsigned char password[] = "password";
    static const unsigned char salt[] = "salt";
    ULONGLONG iterations = 1;
    BCryptBuffer buffers[3];
    BCryptBufferDesc desc;
    BCRYPT_KEY_HANDLE key = NULL;
    unsigned char out[32] = {0};
    ULONG len = 0;
    NTSTATUS status;

    status = BCryptGenerateSymmetricKey(BCRYPT_PBKDF2_ALG_HANDLE, &key, NULL, 0,
            (PUCHAR)password, sizeof(password) - 1, 0);
    if (status)
    {
        printf("BCryptGenerateSymmetricKey on BCRYPT_PBKDF2_ALG_HANDLE returned %#lx, Windows returns 0 FAIL\n", status);
        return 1;
    }

    buffers[0].cbBuffer = sizeof(L"SHA256");
    buffers[0].BufferType = KDF_HASH_ALGORITHM;
    buffers[0].pvBuffer = (void *)L"SHA256";
    buffers[1].cbBuffer = sizeof(salt) - 1;
    buffers[1].BufferType = KDF_SALT;
    buffers[1].pvBuffer = (void *)salt;
    buffers[2].cbBuffer = sizeof(iterations);
    buffers[2].BufferType = KDF_ITERATION_COUNT;
    buffers[2].pvBuffer = &iterations;
    desc.ulVersion = BCRYPTBUFFER_VERSION;
    desc.cBuffers = 3;
    desc.pBuffers = buffers;

    status = BCryptKeyDerivation(key, &desc, out, sizeof(out), &len, 0);
    BCryptDestroyKey(key);
    if (status || len != sizeof(out))
    {
        printf("BCryptKeyDerivation with PBKDF2 and SHA256 returned %#lx and %lu bytes, Windows returns 0 and %u FAIL\n",
                status, len, (unsigned)sizeof(out));
        return 1;
    }
    if (memcmp(out, expected, sizeof(out)))
    {
        printf("PBKDF2-HMAC-SHA256 of \"password\", \"salt\", 1 iteration gave ");
        print_hex(out, sizeof(out));
        printf(", Windows gives ");
        print_hex(expected, sizeof(expected));
        printf(" FAIL\n");
        return 1;
    }
    printf("PBKDF2-HMAC-SHA256 through BCRYPT_PBKDF2_ALG_HANDLE gave ");
    print_hex(out, sizeof(out));
    printf(" ok\n");
    return 0;
}
