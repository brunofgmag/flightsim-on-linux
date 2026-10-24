#include <windows.h>
#include <stdio.h>
#include <stdlib.h>

#define REQUIRED_SLACK 8192
#define SMALL_BLOCKS 4000
#define LARGE_SIZE 0x200000
#define LARGE_BLOCKS 64

static SIZE_T readable_after(const BYTE *p)
{
    MEMORY_BASIC_INFORMATION info;
    const BYTE *cursor = p;
    SIZE_T total = 0;

    while (VirtualQuery(cursor, &info, sizeof(info)) && info.State == MEM_COMMIT &&
           !(info.Protect & (PAGE_NOACCESS | PAGE_GUARD)))
    {
        const BYTE *end = (const BYTE *)info.BaseAddress + info.RegionSize;
        total += end - cursor;
        if (total >= REQUIRED_SLACK) break;
        cursor = end;
    }
    return total;
}

struct tally
{
    const char *name;
    unsigned int blocks;
    unsigned int short_tails;
    SIZE_T least;
    const BYTE *worst;
    SIZE_T worst_size;
};

static void measure(struct tally *tally, const BYTE *block, SIZE_T size)
{
    SIZE_T after = readable_after(block + size);

    tally->blocks++;
    if (after < REQUIRED_SLACK) tally->short_tails++;
    if (after < tally->least)
    {
        tally->least = after;
        tally->worst = block;
        tally->worst_size = size;
    }
}

static void init_tally(struct tally *tally, const char *name)
{
    memset(tally, 0, sizeof(*tally));
    tally->name = name;
    tally->least = (SIZE_T)-1;
}

static BOOL report(const struct tally *tally)
{
    printf("%s: %u blocks, %u with less than %u readable bytes after them, least %lu after the %lu-byte block at %p\n",
           tally->name, tally->blocks, tally->short_tails, REQUIRED_SLACK, (unsigned long)tally->least,
           (unsigned long)tally->worst_size, tally->worst);
    return tally->short_tails == 0;
}

static void small_blocks(struct tally *tally, HANDLE heap)
{
    unsigned int i;

    for (i = 0; i < SMALL_BLOCKS; i++)
    {
        SIZE_T size = 1500 + (i * 37) % 2000;
        BYTE *block = heap ? HeapAlloc(heap, 0, size) : malloc(size);

        if (!block) break;
        measure(tally, block, size);
    }
}

static void large_blocks(struct tally *tally, HANDLE heap)
{
    unsigned int i;

    for (i = 0; i < LARGE_BLOCKS; i++)
    {
        SIZE_T size = LARGE_SIZE - i * 64;
        BYTE *block = HeapAlloc(heap, 0, size);

        if (!block) break;
        measure(tally, block, size);
        HeapFree(heap, 0, block);
    }
}

int main(void)
{
    struct tally growable, crt, large;
    HANDLE heap = HeapCreate(0, 0, 0);
    BOOL ok;

    if (!heap)
    {
        printf("HeapCreate failed\n");
        return 2;
    }
    init_tally(&growable, "HeapCreate heap");
    init_tally(&crt, "malloc");
    init_tally(&large, "large HeapAlloc blocks");
    small_blocks(&growable, heap);
    small_blocks(&crt, NULL);
    large_blocks(&large, heap);

    ok = report(&growable);
    ok = report(&crt) && ok;
    ok = report(&large) && ok;
    printf("%s\n", ok ? "every block has room after it" : "FAIL: a read just past a block can fault (Axis and Ohs copying SimConnect messages)");
    return ok ? 0 : 1;
}
