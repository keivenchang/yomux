"""Declared generation and physical runtime trees for consumers requiring directories."""

load("@rules_go//go:def.bzl", "go_test")

def go_test_pair(name, repo_root_runfile = None, race_shard_count = None, **kwargs):
    """Keep normal and race source/dependency ownership together."""
    kwargs.setdefault("size", "small")
    kwargs.setdefault("timeout", "short")
    if repo_root_runfile:
        env = dict(kwargs.get("env", {}))
        env["YOMUX_REPO_ROOT_RUNFILE"] = repo_root_runfile
        kwargs["env"] = env
    go_test(name = name + "_test", **kwargs)
    race_kwargs = dict(kwargs)
    if race_shard_count == None:
        race_kwargs.pop("shard_count", None)
    else:
        race_kwargs["shard_count"] = race_shard_count
    go_test(name = name + "_race_test", race = "on", **race_kwargs)

def _runtime_tree_impl(ctx):
    output = ctx.actions.declare_directory(ctx.label.name)
    args = ctx.actions.args()
    args.add(output.path)
    for source in ctx.files.srcs:
        args.add(source.path)
        args.add(source.short_path)
    if ctx.attr.frontend:
        frontend = ctx.attr.frontend[DefaultInfo].files.to_list()[0]
        args.add(frontend.path)
        args.add("@frontend@")
    runtime = ctx.toolchains["//:runtime_toolchain_type"]
    ctx.actions.run(
        executable = runtime.python,
        inputs = depset(ctx.files.srcs + ctx.files.frontend + [ctx.file.generator], transitive = [runtime.groups["common"]]),
        outputs = [output],
        arguments = [ctx.file.generator.path, "tree", args],
        env = {"PYTHONDONTWRITEBYTECODE": "1", "TZ": "America/Los_Angeles"},
        mnemonic = "RuntimeTree",
    )
    return [DefaultInfo(files = depset([output]), runfiles = ctx.runfiles(
        files = [output],
        transitive_files = depset(transitive = [runtime.groups[name] for name in ["common"] + ctx.attr.runtime_groups]),
    ))]

runtime_tree = rule(
    implementation = _runtime_tree_impl,
    attrs = {
        "srcs": attr.label_list(allow_files = True),
        "frontend": attr.label(),
        "runtime_groups": attr.string_list(),
        "generator": attr.label(allow_single_file = True, default = "//:tools/bazel_generate.py"),
    },
    toolchains = ["//:runtime_toolchain_type"],
)

def _frontend_impl(ctx):
    output = ctx.actions.declare_directory(ctx.label.name)
    args = ctx.actions.args()
    args.add("--source-root", ".")
    args.add("--output-directory", output.path)
    args.add("--bazel-output")
    ctx.actions.run(
        executable = ctx.executable.builder,
        inputs = ctx.files.srcs,
        outputs = [output],
        arguments = [args],
        mnemonic = "FrontendBuild",
    )
    return [DefaultInfo(files = depset([output]))]

frontend_assets = rule(
    implementation = _frontend_impl,
    attrs = {
        "srcs": attr.label_list(allow_files = True),
        "builder": attr.label(executable = True, cfg = "exec", default = "//tools/staticgen:staticgen"),
    },
)

def _version_impl(ctx):
    output = ctx.actions.declare_file("product_version_generated.go")
    ctx.actions.run(
        executable = ctx.executable.generator,
        inputs = [ctx.file.source],
        outputs = [output],
        arguments = [ctx.file.source.path, output.path],
        mnemonic = "ProductVersion",
    )
    return [DefaultInfo(files = depset([output]))]

product_version = rule(
    implementation = _version_impl,
    attrs = {
        "source": attr.label(allow_single_file = True, default = "//:version.yaml"),
        "generator": attr.label(executable = True, cfg = "exec", default = "//tools/versiongen:versiongen"),
    },
)

def _native_python_impl(ctx):
    runtime = ctx.toolchains["//:runtime_toolchain_type"]
    output = ctx.actions.declare_file(ctx.label.name)
    python_runfile = runtime.python.short_path.removeprefix("../")
    script_runfile = ctx.workspace_name + "/" + ctx.file.script.short_path
    arguments = ctx.attr.arguments
    if ctx.attr.tree:
        arguments = [ctx.workspace_name + "/" + ctx.file.tree.short_path] + arguments
    ctx.actions.write(
        output,
        "#!" + runtime.shell + '\nrunfiles="${RUNFILES_DIR:-${TEST_SRCDIR:-}}"\nif [ ! -f "$runfiles/' + script_runfile + '" ]; then runfiles="$0.runfiles"; fi\nexec "$runfiles/' + python_runfile + '" "$runfiles/' + script_runfile + '" ' + " ".join(["'" + arg.replace("'", "'\"'\"'") + "'" for arg in arguments]) + ' "$@"\n',
        is_executable = True,
    )
    runfiles = ctx.runfiles(files = [runtime.python, ctx.file.script] + ctx.files.data + ctx.files.tree, transitive_files = depset(transitive = [runtime.groups[name] for name in ["common"] + ctx.attr.runtime_groups]))
    for target in ctx.attr.data + ([ctx.attr.tree] if ctx.attr.tree else []):
        runfiles = runfiles.merge(target[DefaultInfo].default_runfiles)
    return [DefaultInfo(executable = output, runfiles = runfiles)]

_native_python_attrs = {
    "script": attr.label(allow_single_file = True, mandatory = True),
    "data": attr.label_list(allow_files = True),
    "tree": attr.label(allow_single_file = True),
    "arguments": attr.string_list(),
    "runtime_groups": attr.string_list(default = ["node", "go", "git_tmux", "browsers", "clients", "compiler", "xterm"]),
}

native_python_binary = rule(
    implementation = _native_python_impl,
    attrs = _native_python_attrs,
    executable = True,
    toolchains = ["//:runtime_toolchain_type"],
)

native_python_test = rule(
    implementation = _native_python_impl,
    attrs = _native_python_attrs,
    test = True,
    toolchains = ["//:runtime_toolchain_type"],
)
