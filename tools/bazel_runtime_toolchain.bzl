"""One Python/runtime toolchain for image-backed and qualified native actions."""

def _runtime_impl(ctx):
    return [platform_common.ToolchainInfo(
        python = ctx.file.python,
        shell = ctx.attr.shell,
        groups = {name: depset(target.files.to_list()) for target, name in ctx.attr.groups.items()},
    )]

runtime_toolchain = rule(
    implementation = _runtime_impl,
    attrs = {
        "python": attr.label(allow_single_file = True, mandatory = True),
        "groups": attr.label_keyed_string_dict(allow_files = True),
        "shell": attr.string(mandatory = True),
    },
)

RUNTIME_GROUPS = ["common", "node", "go", "git_tmux", "browsers", "clients", "compiler", "xterm"]

def _input_group(name):
    if name.startswith(("bazel-registry/", "bazel-repository-cache/", "go-mod-cache/", "module-inputs/", "rootfs/opt/yomux-bazel-registry/", "rootfs/opt/yomux-bazel-repository-cache/", "rootfs/opt/yomux-go-mod-cache/")):
        return None
    leaf = name.split("/")[-1]
    if name.startswith(("chrome/", "rootfs/opt/chrome/", "rootfs/usr/lib/chromium/")) or leaf in ["chrome", "chromium", "chromium-browser", "chromedriver"]:
        return "browsers"
    if name.startswith(("agents/", "rootfs/opt/yomux-agent-clients/")) or leaf in ["claude", "codex", "opencode"]:
        return "clients"
    if name.startswith(("xterm/", "rootfs/opt/xterm/")):
        return "xterm"
    if name.startswith(("go/", "rootfs/usr/local/go/")) or leaf == "go":
        return "go"
    if name.startswith("rootfs/usr/lib/git-core/") or "/git-core/" in name or leaf in ["git", "tmux", "git-remote-http", "git-remote-https"]:
        return "git_tmux"
    if name.startswith(("sdk/", "compiler/", "jdk/", "bazel-native/", "rootfs/usr/include/", "rootfs/usr/local/include/", "rootfs/usr/lib/gcc/", "rootfs/usr/lib/linux/", "python/include/", "rootfs/usr/lib/llvm", "rootfs/usr/libexec/gcc/")):
        return "compiler"
    if leaf.endswith((".a", ".o", ".h", ".hpp", ".pc")) or leaf in ["libc.so", "libm.so"]:
        return "compiler"

    # Triplet prefixes, version suffixes, and linker variants share one owner.
    tools = ["gcc", "g++", "cc", "c++", "cpp", "clang", "clang++", "clangd", "as", "ld", "ar", "nm", "ranlib", "strip", "gcov", "objcopy", "objdump", "addr2line", "c++filt", "elfedit", "gprof", "gprofng", "lto", "dwp", "readelf", "size", "strings", "bazel", "buildifier"]
    if (name.startswith("bin/") or "/bin/" in name) and any([part.split(".")[0] in tools for part in leaf.split("-")]):
        return "compiler"
    if leaf in ["node", "nodejs"]:
        return "node"
    return "common"

def _repository_impl(ctx):
    root = ctx.attr.root
    groups = {name: [] for name in RUNTIME_GROUPS}
    if root:
        manifest = json.decode(ctx.read(root + "/../manifest.json"))
        python = root + "/" + manifest["executables"]["python3"]
        shell = root + "/" + manifest["executables"]["bash"]
        ctx.symlink(root + "/../manifest.json", "manifest.json")
        ctx.symlink(root + "/../manifest.sha256", "manifest.sha256")

        # SDKs and registry overlays contain BUILD files. Flatten declarations
        # so those data files cannot accidentally become Bazel package barriers.
        # Link records and empty directories are covered by the locked manifest.
        for index, name in enumerate(sorted(manifest["files"])):
            group = _input_group(name)
            if group and "sha256" in manifest["files"][name]:
                link = "inputs/" + group + "/" + str(index)
                ctx.symlink(root + "/" + name, link)
                groups[group].append(link)
    else:
        # The Docker contract qualifies these absolute image-owned paths and
        # keys its cache by the immutable image digest before action lookup.
        python = ctx.attr.diagnostic_python or str(ctx.which("python3") or "")
        if not python:
            fail("native diagnostics require Python on PATH or YOMUX_DIAGNOSTIC_PYTHON")
        shell = "/bin/bash"

    ctx.file("python", "#!" + shell + '\nexec "' + python + '" "$@"\n', executable = True)
    ctx.file("BUILD.bazel", '''load("@yomux//:tools/bazel_runtime_toolchain.bzl", "runtime_toolchain")
package(default_visibility = ["//visibility:public"])
exports_files(["python"])
%s
runtime_toolchain(name = "implementation", python = ":python", groups = %s, shell = %s)
toolchain(name = "toolchain", toolchain = ":implementation", toolchain_type = "@yomux//:runtime_toolchain_type")
''' % ("\n".join(["filegroup(name = %s, srcs = %s)" % (repr(name), repr(groups[name])) for name in RUNTIME_GROUPS]), repr({":" + name: name for name in RUNTIME_GROUPS}), repr(shell)))

_runtime_repository = repository_rule(
    implementation = _repository_impl,
    attrs = {"root": attr.string(), "diagnostic_python": attr.string()},
    environ = ["PATH"],
    local = True,
)

def _extension_impl(ctx):
    _runtime_repository(
        name = "yomux_runtime",
        root = ctx.getenv("YOMUX_NATIVE_BUNDLE", ""),
        diagnostic_python = ctx.getenv("YOMUX_DIAGNOSTIC_PYTHON", ""),
    )
    return ctx.extension_metadata(reproducible = True)

runtime = module_extension(
    implementation = _extension_impl,
    environ = ["YOMUX_NATIVE_BUNDLE", "YOMUX_DIAGNOSTIC_PYTHON"],
)
