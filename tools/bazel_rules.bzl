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
    ctx.actions.run_shell(
        inputs = ctx.files.srcs + ctx.files.frontend,
        outputs = [output],
        arguments = [args],
        command = """set -eu
out="$1"; shift
mkdir -p "$out"
while [ "$#" -gt 0 ]; do
  source="$1"; relative="$2"; shift 2
  if [ "$relative" = "@frontend@" ]; then
    mkdir -p "$out/static"
    cp -RL "$source/." "$out/static/"
    continue
  fi
  mkdir -p "$out/$(dirname "$relative")"
  if [ -d "$source" ]; then
    mkdir -p "$out/$relative"
    cp -RL "$source/." "$out/$relative/"
  else
    cp -L "$source" "$out/$relative"
  fi
done
""",
        use_default_shell_env = True,
        mnemonic = "RuntimeTree",
    )
    return [DefaultInfo(files = depset([output]), runfiles = ctx.runfiles(files = [output]))]

runtime_tree = rule(
    implementation = _runtime_tree_impl,
    attrs = {
        "srcs": attr.label_list(allow_files = True),
        "frontend": attr.label(),
    },
)

def _frontend_impl(ctx):
    output = ctx.actions.declare_directory(ctx.label.name)
    ctx.actions.run_shell(
        inputs = ctx.files.srcs,
        outputs = [output],
        arguments = [ctx.file.builder.path, output.path],
        command = 'set -eu; PYTHONDONTWRITEBYTECODE=1 python3 "$1" --source-root . --output-directory "$2"; PYTHONDONTWRITEBYTECODE=1 python3 "$1" --source-root . --output-directory "$2" --check',
        use_default_shell_env = True,
        mnemonic = "FrontendBuild",
    )
    return [DefaultInfo(files = depset([output]))]

frontend_assets = rule(
    implementation = _frontend_impl,
    attrs = {
        "srcs": attr.label_list(allow_files = True),
        "builder": attr.label(allow_single_file = True, default = "//:tools/static_build.py"),
    },
)
