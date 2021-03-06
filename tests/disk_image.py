#!/usr/bin/python3
import subprocess
import tempfile
import shutil
import os
import options

HYPER_ISO_BOOT_RECORD = "hyper_iso_boot"
FS_IMAGE_RELPATH = "boot"

normal_boot_cfgs = \
"""
default-entry = {default_entry}
[i386]
protocol=ultra
cmdline    = {cmdline}
binary     = "/boot/kernel_i686"
video-mode:
    format = xrgb8888
[amd64_lower_half]
protocol=ultra
cmdline    = {cmdline}
binary     = "/boot/kernel_amd64_lower_half"
video-mode:
    format = xrgb8888
[amd64_higher_half]
protocol=ultra
cmdline = {cmdline}
binary:
    path = "/boot/kernel_amd64_higher_half"
    allocate-anywhere = true
video-mode:
    format = xrgb8888
higher-half-exclusive = true
"""

def make_normal_boot_config(default_entry, cmdline):
    return normal_boot_cfgs.format(default_entry=default_entry,
                                   cmdline=cmdline)


def file_resize_to_mib(path, mib):
    subprocess.check_call(["dd", "if=/dev/zero", f"of={path}",
                           "bs=1MiB", f"count={mib}"])


def image_partition(path, br_type, fs_type, align_mib):
    label = "gpt" if br_type == "GPT" else "msdos"
    subprocess.check_call(["parted", "-s", path, "mklabel", label])

    part_type = "primary" if br_type == "MBR" else "test-partition"

    fs_type = fs_type.lower()
    if fs_type == "fat12":
        fs_type = "fat16" # parted doesn't support fat12 labels

    subprocess.check_call(["parted", "-s", path, "mkpart", part_type,
                           fs_type, f"{align_mib}MiB", "100%"])


def get_fs_mib_size_for_type(fs_type):
    if fs_type == "FAT12":
        return 3

    if fs_type == "FAT16":
        return 32

    if fs_type == "FAT32":
        return 64

    raise RuntimeError(f"Unknown filesystem type {fs_type}")


def fat_recursive_copy(raw_fs_path, file):
    subprocess.check_call(["mcopy", "-Q", "-i", raw_fs_path, "-s", file, "::"])


def fat_fill(raw_fs_path, root_dir):
    for f in os.listdir(root_dir):
        full_path = os.path.abspath(os.path.join(root_dir, f))
        fat_recursive_copy(raw_fs_path, full_path)


def make_fat(raw_fs_path, size, force_fat32):
    cr_args = ["mformat", "-i", raw_fs_path]
    if force_fat32:
        cr_args.append("-F")

    subprocess.check_call(cr_args)


def make_iso(image_path, root_path, has_uefi, has_bios):
    # Make the disk itself
    xorriso_args = ["xorriso", "-as", "mkisofs"]

    bios_args = [
        "-b", f"{FS_IMAGE_RELPATH}/{HYPER_ISO_BOOT_RECORD}",
        "-no-emul-boot", "-boot-load-size", "4", "-boot-info-table"
    ]
    if has_bios:
        xorriso_args.extend(bios_args)

    uefi_args = [
        "--efi-boot", "efi_esp", "-efi-boot-part", "--efi-boot-image"
    ]
    if has_uefi:
        # Make the EFI ESP partition
        fat_image = os.path.join(root_path, "efi_esp")
        file_resize_to_mib(fat_image, 1)
        make_fat(fat_image, 1, False)
        fat_recursive_copy(fat_image, os.path.join(root_path, "EFI"))

        xorriso_args.extend(uefi_args)

    xorriso_args.extend(["--protective-msdos-label", root_path,
                         "-o", image_path])

    subprocess.check_call(xorriso_args)


def image_embed(image_path, mib_offset, fs_image):
    subprocess.check_call(["dd", f"if={fs_image}", f"seek={mib_offset}",
                           "bs=1MiB", f"of={image_path}", "conv=notrunc"])


def make_fs(image_path, fs_type, image_mib_offset, size, root_path,
            has_uefi, has_iso_br):
    if fs_type == "ISO9660":
        return make_iso(image_path, root_path, has_uefi, has_iso_br)

    with tempfile.NamedTemporaryFile() as tf:
        file_resize_to_mib(tf.name, size)

        if fs_type.startswith("FAT"):
            make_fat(tf.name, size, fs_type == "FAT32")
            fat_fill(tf.name, root_path)
        else:
            raise RuntimeError(f"Unknown filesystem type {fs_type}")

        image_embed(image_path, image_mib_offset, tf.name)


class DiskImage:
    # always align partitions at 1 MiB
    part_align_mibs = 1

    def __init__(self, fs_root_dir, br_type, fs_type, has_uefi, has_iso_br,
                 installer_path=None, out_path=None):
        self.__fs_root_dir = fs_root_dir
        self.__br_type = br_type
        self.__fs_type = fs_type
        self.__path = out_path if out_path else tempfile.mkstemp()[1]
        fs_size = 0

        is_iso = self.fs_type == "ISO9660"

        if not is_iso:
            fs_size = get_fs_mib_size_for_type(self.fs_type)
            file_resize_to_mib(self.__path, fs_size + DiskImage.part_align_mibs)

            if self.__br_type == "MBR" or self.__br_type == "GPT":
                image_partition(self.__path, self.__br_type, self.__fs_type,
                                DiskImage.part_align_mibs)

        make_fs(self.__path, self.__fs_type, DiskImage.part_align_mibs,
                fs_size, self.__fs_root_dir, has_uefi, has_iso_br)

        should_install = installer_path is not None and self.__br_type != "GPT"

        # Hybrid boot depends on having stage2 pointed to by el-torito
        if is_iso:
            should_install = should_install and has_iso_br

        if should_install:
            subprocess.check_call([installer_path, self.__path])

    @property
    def br_type(self):
        return self.__br_type

    def is_cd(self):
        return self.__br_type == "CD"

    @property
    def fs_type(self):
        return self.__fs_type

    @property
    def path(self):
        return self.__path

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        os.remove(self.__path)


def prepare_test_fs_root(opt_getter) -> str:
    uefi_path = opt_getter(options.HYPER_UEFI_OPT)
    iso_br_path = opt_getter(options.HYPER_ISO_BR_OPT)
    kernel_dir = opt_getter(options.KERNEL_DIR_OPT)
    test_dir = opt_getter(options.INTERM_DIR_OPT)

    root_dir = os.path.join(test_dir, FS_IMAGE_RELPATH)
    uefi_dir = os.path.join(test_dir, "EFI/BOOT")

    i386_krnl = os.path.join(kernel_dir, "kernel_i686")
    amd64_lh_krnl = os.path.join(kernel_dir, "kernel_amd64_lower_half")
    amd64_hh_krnl = os.path.join(kernel_dir, "kernel_amd64_higher_half")

    os.mkdir(test_dir)
    os.mkdir(root_dir)

    if uefi_path:
        os.makedirs(uefi_dir)
        shutil.copy(uefi_path, os.path.join(uefi_dir, "BOOTX64.EFI"))

    if iso_br_path:
        shutil.copy(iso_br_path, os.path.join(root_dir, HYPER_ISO_BOOT_RECORD))

    shutil.copy(i386_krnl, root_dir)
    shutil.copy(amd64_lh_krnl, root_dir)
    shutil.copy(amd64_hh_krnl, root_dir)

    return test_dir


def fs_root_set_cfg(root_path, cfg):
    path = os.path.join(root_path, FS_IMAGE_RELPATH)
    path = os.path.join(path, "hyper.cfg")

    with open(path, "w") as hc:
        hc.write(cfg)