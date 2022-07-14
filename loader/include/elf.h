#pragma once

#include "common/types.h"

struct binary_info {
    u64 entrypoint_address;

    u64 virtual_base;
    u64 virtual_ceiling;

    u64 physical_base;
    u64 physical_ceiling;

    u8 bitness;
    bool kernel_range_is_direct_map;
};

struct load_result {
    struct binary_info info;
    const char *error_msg;
};

bool elf_load(void *file_data, size_t size, bool use_va, bool allocate_anywhere,
              u32 binary_alloc_type, struct load_result *res);
u8 elf_bitness(void *file_data, size_t size);