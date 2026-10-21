#include <windows.h>
#include <wincrypt.h>
#include <wintrust.h>
#include <mssip.h>
#include <bcrypt.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define CERTIFICATE_DUMMY_SIZE 20
#define CERTIFICATE_TABLE_SIZE 32
#define SHA256_SIZE 32
#define PE32_PLUS_MAGIC 0x20b

struct image
{
    BYTE *data;
    DWORD size;
    DWORD checksum_offset;
    DWORD security_entry_offset;
    DWORD certificate_offset;
};

static int load_signed_copy(const WCHAR *source, struct image *image)
{
    HANDLE file = CreateFileW(source, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    DWORD file_size, read = 0, pe_offset;
    WIN_CERTIFICATE *certificate;

    if (file == INVALID_HANDLE_VALUE) return 2;
    file_size = GetFileSize(file, NULL);
    image->certificate_offset = (file_size + 7) & ~7u;
    image->size = image->certificate_offset + CERTIFICATE_TABLE_SIZE;
    image->data = calloc(1, image->size);
    if (!image->data || !ReadFile(file, image->data, file_size, &read, NULL) || read != file_size)
    {
        CloseHandle(file);
        return 2;
    }
    CloseHandle(file);

    pe_offset = *(DWORD *)(image->data + 0x3c);
    if (image->data[0] != 'M' || image->data[1] != 'Z' || memcmp(image->data + pe_offset, "PE\0\0", 4)
            || *(WORD *)(image->data + pe_offset + 24) != PE32_PLUS_MAGIC)
        return 2;
    image->checksum_offset = pe_offset + 24 + 64;
    image->security_entry_offset = pe_offset + 24 + 112 + 8 * IMAGE_DIRECTORY_ENTRY_SECURITY;

    certificate = (WIN_CERTIFICATE *)(image->data + image->certificate_offset);
    certificate->dwLength = offsetof(WIN_CERTIFICATE, bCertificate) + CERTIFICATE_DUMMY_SIZE;
    certificate->wRevision = WIN_CERT_REVISION_2_0;
    certificate->wCertificateType = WIN_CERT_TYPE_PKCS_SIGNED_DATA;
    memset(certificate->bCertificate, 0xa5, CERTIFICATE_DUMMY_SIZE);
    *(DWORD *)(image->data + image->security_entry_offset) = image->certificate_offset;
    *(DWORD *)(image->data + image->security_entry_offset + 4) = CERTIFICATE_TABLE_SIZE;
    return 0;
}

static int write_file(const WCHAR *path, const BYTE *data, DWORD size)
{
    HANDLE file = CreateFileW(path, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    DWORD written = 0;
    BOOL ok;

    if (file == INVALID_HANDLE_VALUE) return 2;
    ok = WriteFile(file, data, size, &written, NULL) && written == size;
    CloseHandle(file);
    return ok ? 0 : 2;
}

static int expected_digest(const struct image *image, BYTE *digest)
{
    const DWORD ranges[3][2] =
    {
        {0, image->checksum_offset},
        {image->checksum_offset + 4, image->security_entry_offset},
        {image->security_entry_offset + 8, image->certificate_offset},
    };
    BCRYPT_ALG_HANDLE algorithm = NULL;
    BCRYPT_HASH_HANDLE hash = NULL;
    NTSTATUS status;
    int i;

    status = BCryptOpenAlgorithmProvider(&algorithm, BCRYPT_SHA256_ALGORITHM, NULL, 0);
    if (!status) status = BCryptCreateHash(algorithm, &hash, NULL, 0, NULL, 0, 0);
    for (i = 0; !status && i < 3; i++)
        status = BCryptHashData(hash, image->data + ranges[i][0], ranges[i][1] - ranges[i][0], 0);
    if (!status) status = BCryptFinishHash(hash, digest, SHA256_SIZE, 0);
    if (hash) BCryptDestroyHash(hash);
    if (algorithm) BCryptCloseAlgorithmProvider(algorithm, 0);
    return status ? 2 : 0;
}

static void print_hex(const BYTE *data, DWORD size)
{
    DWORD i;
    for (i = 0; i < size; i++) printf("%02x", data[i]);
}

static int verify_indirect_data(const SIP_INDIRECT_DATA *indirect, const BYTE *expected)
{
    if (!indirect->Data.pszObjId || strcmp(indirect->Data.pszObjId, SPC_PE_IMAGE_DATA_OBJID))
    {
        printf("the indirect data of a PE file has the type %s, Windows returns %s FAIL\n",
                indirect->Data.pszObjId ? indirect->Data.pszObjId : "none", SPC_PE_IMAGE_DATA_OBJID);
        return 1;
    }
    if (indirect->Digest.cbData != SHA256_SIZE || memcmp(indirect->Digest.pbData, expected, SHA256_SIZE))
    {
        printf("the SHA-256 digest of the signed PE file is ");
        print_hex(indirect->Digest.pbData, indirect->Digest.cbData);
        printf(", Windows returns ");
        print_hex(expected, SHA256_SIZE);
        printf(" FAIL\n");
        return 1;
    }
    printf("CryptSIPCreateIndirectData of a signed PE file returned type %s and the SHA-256 digest ", SPC_PE_IMAGE_DATA_OBJID);
    print_hex(indirect->Digest.pbData, indirect->Digest.cbData);
    printf(" ok\n");
    return 0;
}

static int measure(const WCHAR *path, const struct image *image)
{
    BYTE expected[SHA256_SIZE];
    SIP_SUBJECTINFO subject = {0};
    SIP_INDIRECT_DATA *indirect;
    GUID subject_type;
    DWORD size = 0;
    int status;

    if (expected_digest(image, expected)) return 2;
    if (!CryptSIPRetrieveSubjectGuid(path, NULL, &subject_type))
    {
        printf("CryptSIPRetrieveSubjectGuid failed with error %lu\n", GetLastError());
        return 2;
    }
    subject.cbSize = sizeof(subject);
    subject.pgSubjectType = &subject_type;
    subject.pwsFileName = path;
    subject.DigestAlgorithm.pszObjId = (LPSTR)szOID_NIST_sha256;

    if (!CryptSIPCreateIndirectData(&subject, &size, NULL) || !size)
    {
        printf("CryptSIPCreateIndirectData asked for the size returned FALSE with error %lu, Windows returns TRUE FAIL\n",
                GetLastError());
        return 1;
    }
    indirect = calloc(1, size);
    if (!indirect) return 2;
    if (!CryptSIPCreateIndirectData(&subject, &size, indirect))
    {
        printf("CryptSIPCreateIndirectData with a %lu byte buffer returned FALSE with error %lu, Windows returns TRUE FAIL\n",
                size, GetLastError());
        free(indirect);
        return 1;
    }
    status = verify_indirect_data(indirect, expected);
    free(indirect);
    return status;
}

int main(void)
{
    WCHAR self[MAX_PATH], path[MAX_PATH];
    struct image image;
    int status;

    GetModuleFileNameW(NULL, self, MAX_PATH);
    GetTempPathW(MAX_PATH, path);
    wcscat(path, L"sip-indirect-data-test.exe");
    status = load_signed_copy(self, &image);
    if (!status) status = write_file(path, image.data, image.size);
    if (status)
    {
        printf("could not make a copy of the test with a certificate table\n");
        return 2;
    }
    status = measure(path, &image);
    DeleteFileW(path);
    return status;
}
