#include <windows.h>
#include <stdio.h>
#include <stdlib.h>

#define FIXED_HEAP_SIZE (512 * 1024 * 1024)
#define HOLES 60000
#define HOLE_SIZE 1496
#define GUARD_SIZE 32
#define REQUEST_SIZE 1512
#define REQUESTS 201
#define LIMIT_MICROSECONDS 50.0
#define CHURN_ROUNDS 4096
#define HEAP_COMPATIBILITY_LFH 2

static int compare_doubles(const void *a, const void *b)
{
    double left = *(const double *)a, right = *(const double *)b;

    return (left > right) - (left < right);
}

static void shuffle(unsigned int *order, unsigned int count)
{
    unsigned int i, seed = 12345;

    for (i = 0; i < count; i++) order[i] = i;
    for (i = count - 1; i > 0; i--)
    {
        unsigned int other, swap;

        seed = seed * 1103515245 + 12345;
        other = (seed >> 8) % (i + 1);
        swap = order[i];
        order[i] = order[other];
        order[other] = swap;
    }
}

static BOOL scan_stays_short(void)
{
    static double spent[REQUESTS];
    LARGE_INTEGER frequency, before, after;
    HANDLE heap = HeapCreate(0, 0, FIXED_HEAP_SIZE);
    BYTE **holes = malloc(HOLES * sizeof(*holes));
    unsigned int *order = malloc(HOLES * sizeof(*order));
    unsigned int i;
    BOOL valid;

    if (!heap || !holes || !order)
    {
        printf("could not set up the heap\n");
        return FALSE;
    }
    for (i = 0; i < HOLES; i++)
    {
        holes[i] = HeapAlloc(heap, 0, HOLE_SIZE);
        if (!holes[i] || !HeapAlloc(heap, 0, GUARD_SIZE))
        {
            printf("allocation %u failed\n", i);
            return FALSE;
        }
    }
    shuffle(order, HOLES);
    for (i = 0; i < HOLES; i++) HeapFree(heap, 0, holes[order[i]]);

    QueryPerformanceFrequency(&frequency);
    for (i = 0; i < REQUESTS; i++)
    {
        BYTE *block;

        QueryPerformanceCounter(&before);
        block = HeapAlloc(heap, 0, REQUEST_SIZE);
        QueryPerformanceCounter(&after);
        if (!block || HeapSize(heap, 0, block) < REQUEST_SIZE)
        {
            printf("request %u failed\n", i);
            return FALSE;
        }
        memset(block, 0x5a, REQUEST_SIZE);
        spent[i] = (after.QuadPart - before.QuadPart) * 1000000.0 / frequency.QuadPart;
    }
    valid = HeapValidate(heap, 0, NULL);
    qsort(spent, REQUESTS, sizeof(*spent), compare_doubles);
    printf("%u free blocks too small: a %u-byte request took %.1f us (median), %.1f us (worst), heap valid: %d\n",
           HOLES, REQUEST_SIZE, spent[REQUESTS / 2], spent[REQUESTS - 1], valid);
    HeapDestroy(heap);
    free(holes);
    free(order);
    return valid && spent[REQUESTS / 2] < LIMIT_MICROSECONDS;
}

static BOOL churn_reaches_fast_path(void)
{
    HANDLE heap = HeapCreate(0, 0, 0);
    ULONG compatibility = 0;
    unsigned int i;

    if (!heap)
    {
        printf("HeapCreate failed\n");
        return FALSE;
    }
    for (i = 0; i < CHURN_ROUNDS; i++) HeapFree(heap, 0, HeapAlloc(heap, 0, REQUEST_SIZE));
    HeapQueryInformation(heap, HeapCompatibilityInformation, &compatibility, sizeof(compatibility), NULL);
    printf("%u rounds of a %u-byte block: heap compatibility %lu, %d means the low fragmentation heap\n",
           CHURN_ROUNDS, REQUEST_SIZE, compatibility, HEAP_COMPATIBILITY_LFH);
    HeapDestroy(heap);
    return compatibility == HEAP_COMPATIBILITY_LFH;
}

int main(void)
{
    BOOL scan = scan_stays_short();
    BOOL churn = churn_reaches_fast_path();

    printf("%s\n", scan && churn ? "the heap stays fast" : "FAIL: an allocation can hold the heap lock for milliseconds (FSLabs stutter once a second)");
    return scan && churn ? 0 : 1;
}
