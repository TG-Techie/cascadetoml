import typer
import tomlkit
import pathlib
import typing
import parse

app = typer.Typer()

refactor_app = typer.Typer()
app.add_typer(refactor_app, name="refactor")

cascade_app = typer.Typer()
app.add_typer(cascade_app, name="cascade")

def _cascade(paths: typing.List[pathlib.Path]) -> tomlkit.document:
    output_doc = tomlkit.document()

    root_cache = {}
    for path in paths:
        full_path = path.resolve()
        # Find the cascade root
        root = None
        for parent in full_path.parents:
            possible_root = parent / ".cascade.toml"
            if possible_root.is_file():
                root = possible_root
                break
        if not root:
            print("No root found for", path)
            continue

        if root not in root_cache:
            loaded_info = tomlkit.parse(root.read_text())
            root_cache[root] = loaded_info
            implied_paths = []
            for path_template in loaded_info["paths"]:
                path_template = pathlib.Path(path_template)
                for parent in path_template.parents:
                    if not parent.name:
                        continue
                    implied_paths.append(str(parent / (parent.name + ".toml")))
            # Extend is broken on tomlkit array so do it manually
            for implied_path in implied_paths:
                loaded_info["paths"].append(implied_path)
        root_info = root_cache[root]
        root_relative = full_path.relative_to(root.parent)

        template = list(root.parent.glob("*.template.toml"))
        if not template or len(template) > 1:
            print("No template found for", path)
            continue

        object_type = template[0].name[:-len(".template.toml")]

        try:
            parsed_leaf = tomlkit.parse(full_path.read_text())
        except tomlkit.exceptions.ParseError as e:
            print("Error parsing {}".format(path))
            print(e)
            continue

        c = tomlkit.comment("Data for path: {}".format(root_relative))
        if len(paths) > 1:
            output_table = tomlkit.table()
            output_table.add(c)
            output_table.add(tomlkit.nl())
            if object_type not in output_doc:
                output_doc[object_type] = tomlkit.aot() # short for array of tables
            output_doc[object_type].append(output_table)
        else:
            output_doc.add(c)
            output_table = output_doc

        # print(path, path.stem, path.parent.stem)
        parsed_path = None
        template_path = None
        for template in root_info["paths"]:
            found = parse.search(template, str(root_relative))
            if found:
                parsed_path = found
                template_path = template
                break

        if parsed_path:
            output_table.add(tomlkit.comment("Data inferred from the path: {}".format(template_path)))
            for k in parsed_path.named:
                output_table[k] = parsed_path.named[k]

        for parent in reversed(full_path.parents):
            if not parent.is_relative_to(root.parent):
                continue
            if parent.stem == full_path.parent.stem:
                continue

            parent_toml = parent / (parent.stem + ".toml")

            if not parent_toml.is_file():
                continue

            parsed_parent = {}
            try:
                parsed_parent = tomlkit.parse(parent_toml.read_text())
            except tomlkit.exceptions.ParseError as e:
                print("Error parsing {}".format(path))
                print(e)
                raise typer.Exit(code=3)

            output_table.add(tomlkit.nl())
            output_table.add(tomlkit.comment("Data from {}".format(parent_toml.relative_to(root.parent))))
            for item in parsed_parent.body:
                key, value = item
                output_table.add(key, value)

        output_table.add(tomlkit.nl())
        output_table.add(tomlkit.comment("Data from {}".format(root_relative)))
        for item in parsed_leaf.body:
            key, value = item
            output_table.add(key, value)
    return output_doc

@cascade_app.command()
def files(paths: typing.List[pathlib.Path]):
    """Produce cascaded toml objects for each given path."""
    print("cascade")
    output_doc = _cascade(paths)
    print(tomlkit.dumps(output_doc))

@cascade_app.command()
def filter(root: pathlib.Path = typer.Option(".", help="Path to a cascade root. (Where `.cascade.toml` lives.)"),
           filters: typing.List[str] = typer.Argument(None, help="TOML values that must match")):
    """Produce cascaded toml objects for each given path."""
    root_toml = root / ".cascade.toml"
    if not root_toml.exists():
        print("Missing root .cascade.toml")
        raise typer.Exit(code=5)


    template = list(root.glob("*.template.toml"))
    if not template or len(template) > 1:
        print("No template found for", path)
        raise typer.Exit(code=6)

    object_type = template[0].name[:-len(".template.toml")]

    acceptable_values = {}
    for f in filters:
        parsed = tomlkit.parse(f)
        for k in parsed:
            if k not in acceptable_values:
                acceptable_values[k] = []
            acceptable_values[k].append(parsed[k])

    output_doc = _cascade(list(root.glob("*/**/*.toml")))

    for i in range(len(output_doc[object_type]) - 1, -1, -1):
        entry = output_doc[object_type][i]
        for k in acceptable_values:
            if k not in entry or entry[k] not in acceptable_values[k]:
                del output_doc[object_type].body[i]

    print(tomlkit.dumps(output_doc))

@app.command()
def check(root: pathlib.Path = typer.Option(".", help="Path to a cascade root. (Where `.cascade.toml` lives.)")):
    """Check that all toml under the given path are parse and match the template."""
    possible_templates = list(root.glob("*.template.toml"))
    if len(possible_templates) > 1:
        print("Only one template supported")
        raise typer.Exit(code=1)
    if not possible_templates:
        print("Template required")
        raise typer.Exit(code=2)
    toml_template = tomlkit.parse(possible_templates[0].read_text())

    error_count = 0
    for tomlfile in root.glob("*/**/*.toml"):
        root_relative = tomlfile.relative_to(root)
        errors = []
        parsed_leaf = {}
        try:
            parsed_leaf = tomlkit.parse(tomlfile.read_text())
        except tomlkit.exceptions.ParseError as e:
            errors.append("Parse error: {}".format(e))
        for k in parsed_leaf:
            if k not in toml_template:
                errors.append("Unknown key {}".format(k))
            elif type(toml_template[k]) != type(parsed_leaf[k]):
                errors.append("Type mismatch for key {}".format(k))
        error_count += len(errors)
        if errors:
            print("Error(s) in {}:".format(root_relative))
            for e in errors:
                print("\t" + e)
            print()

    if error_count > 0:
        raise typer.Exit(code=-1 * error_count)

# Recursion!
def _coalesce(path: pathlib.Path):
    if path.is_dir():
        shared = None
        for entry in path.iterdir():
            if entry.stem.startswith("."):
                continue
            if entry.stem == path.name:
                continue
            data = _coalesce(entry)
            if data:
                if shared is None:
                    shared = data
                    continue
                different_keys = []
                for k in shared:
                    if k not in data or shared[k] != data[k]:
                        different_keys.append(k)
                for k in different_keys:
                    del shared[k]
            elif data is {}:
                shared = {}
        if not shared:
            return shared
        dir_toml = path / (path.name + ".toml")
        existing = tomlkit.document()
        if dir_toml.exists():
            try:
                existing = tomlkit.parse(dir_toml.read_text())
            except tomlkit.exceptions.ParseError as e:
                # {} means nothing is shared
                return {}
        for k in shared:
            existing.append(k, shared[k])
        dir_toml.write_text(tomlkit.dumps(existing))
        for entry in path.iterdir():
            if entry.stem.startswith("."):
                continue
            if entry.stem == path.stem:
                continue
            if entry.is_dir():
                entry = entry / (entry.name + ".toml")
            if entry.is_file() and len(entry.suffixes) == 1 and entry.suffix == ".toml":
                existing = tomlkit.parse(entry.read_text())
                for k in shared:
                    if k in existing:
                        del existing[k]
                entry.write_text(tomlkit.dumps(existing))
        return shared

    elif path.is_file() and len(path.suffixes) == 1 and path.suffix == ".toml":
        try:
            return tomlkit.parse(path.read_text())
        except tomlkit.exceptions.ParseError as e:
            # {} means nothing is shared
            return {}
    return None


@refactor_app.command()
def coalesce(root: pathlib.Path = typer.Option(".", help="Path to a cascade root. (Where `.cascade.toml` lives.)")):
    """Move common definitions to shared tomls"""
    _coalesce(root)

if __name__ == "__main__":
    app()
