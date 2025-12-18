# coding=utf-8
import os
import pathlib
from xml.etree.ElementTree import Element

from modules.ast import create_ast_file, read_ast_file
from modules.logger import log_debug
from modules.utils import replace_node, delete_node, create_array_literal_values, get_array_literal_values


def opt_invoke_expression(ast, parents):
    ret = False
    p = pathlib.Path("tmp.ps1")

    for node in ast.iter("CommandElements"):
        subnodes = list(node)
        if len(subnodes) == 2:
            if subnodes[0].tag == "StringConstantExpressionAst" and subnodes[0].attrib[
                    "StringConstantType"] == "BareWord" and subnodes[0].text == "Invoke-Expression":
                if subnodes[1].tag == "StringConstantExpressionAst" and subnodes[1].attrib[
                        "StringConstantType"] != "BareWord":

                    script_content = subnodes[1].text

                    with open(p, "w") as tmp:
                        tmp.write(script_content)

                    if create_ast_file(p, None):
                        if sub_ast := read_ast_file(p.with_suffix(".xml")):
                            log_debug("Replace Invoke-Expression by expression AST")

                            sub_tree = sub_ast.getroot()
                            if sub_tree.tag == "ScriptBlockAst":
                                sub_tree = sub_tree[0]
                            replace_node(ast, subnodes[0], sub_tree, until="CommandAst", parents=parents)

                            ret = True
                            break

    try:
        os.remove(p.with_suffix(".xml"))
        os.remove(p.with_suffix(".ps1"))
    except Exception:
        pass

    return ret


def opt_invoke_replace_string(ast, parents):
    for node in ast.iter("InvokeMemberExpressionAst"):
        subnodes = list(node)

        if len(subnodes) < 3:
            continue

        if subnodes[2].tag == 'StringConstantExpressionAst' and \
                subnodes[2].attrib["StringConstantType"] == "BareWord" and \
                subnodes[2].text.lower() == "replace":
            if subnodes[1].tag == 'StringConstantExpressionAst' and \
                    subnodes[1].attrib["StringConstantType"] != "BareWord":
                arguments = subnodes[0]
                if arguments is not None:
                    argument_values = []

                    for element in list(arguments):
                        if element.tag == "StringConstantExpressionAst":
                            argument_values.append(element.text)

                    if len(argument_values) != 2:
                        continue

                    formatted = subnodes[1].text.replace(argument_values[0], argument_values[1])

                    log_debug("Apply replace method on '%s'" % formatted)

                    new_element = Element("StringConstantExpressionAst",
                                          {
                                              "StringConstantType": "SingleQuoted",
                                              "StaticType": "string",
                                          })
                    new_element.text = formatted

                    replace_node(ast, node, new_element, parents=parents)

                    return True
    return False


def opt_invoke_split_string(ast, parents):
    for node in ast.iter("InvokeMemberExpressionAst"):
        subnodes = list(node)

        if len(subnodes) < 3:
            continue

        if subnodes[2].tag == 'StringConstantExpressionAst' and \
                subnodes[2].attrib["StringConstantType"] == "BareWord" and \
                subnodes[2].text.lower() == "split":
            if subnodes[1].tag == 'StringConstantExpressionAst' and \
                    subnodes[1].attrib["StringConstantType"] != "BareWord":
                argument = subnodes[0]
                if argument is not None:
                    argument = argument.find("StringConstantExpressionAst")
                    if argument is not None:
                        splitted = subnodes[1].text.split(argument.text)

                        new_array_ast = create_array_literal_values(splitted)

                        log_debug("Apply split operation to %s" % splitted)

                        replace_node(ast, node, new_array_ast, parents=parents)
                        return True
    return False


def try_reverse_variable_if_not_used(ast, variable, before_node, parents):
    for node in ast.iter():
        if node.tag == "VariableExpressionAst" and node.attrib["VariablePath"].lower() == variable.lower():
            parent = parents[node]
            if parent is not None and parents[node].tag == "AssignmentStatementAst":
                operands = parent.find("CommandExpressionAst")
                if operands.tag == "CommandExpressionAst":
                    operands = operands.find("ArrayLiteralAst")
                if operands is not None:
                    operands = operands.find("Elements")

                    new_element = Element("Elements")
                    for element in operands:
                        new_element.insert(0, element)

                    replace_node(ast, operands, new_element, parents=parents)

                    log_debug(f"Apply reverse method to variable ${variable}")

                    return True
            else:
                return False

    return False


def opt_invoke_reverse_array(ast, parents):
    for node in ast.iter("InvokeMemberExpressionAst"):
        subnodes = list(node)
        if subnodes[1].tag == "TypeExpressionAst" and subnodes[1].attrib["TypeName"].lower() == "array":
            if subnodes[2].tag == "StringConstantExpressionAst" and \
                    subnodes[2].attrib["StringConstantType"] == "BareWord":
                argument = subnodes[0].find("VariableExpressionAst")
                if argument is not None:
                    variable = argument.attrib["VariablePath"]

                    if try_reverse_variable_if_not_used(ast, variable, node, parents):
                        delete_node(ast, node, parents=parents)

                        return True

    return False


def opt_invoke_array_foreach(ast: Element, parents):
    """
    @(132,176,173,175,128,163,177,167,244,246,145,182,176,171,172,165).ForEach({$_ -bxor 194})
    """
    for node in ast.iter("InvokeMemberExpressionAst"):
        subnodes = list(node)
        if len(subnodes) == 3 and subnodes[0].tag == "Arguments" \
                and subnodes[1].tag == "ArrayExpressionAst" \
                and subnodes[2].tag == "StringConstantExpressionAst" \
                and subnodes[2].text == "ForEach-Object":

            command = next(subnodes[0].iter("CommandExpressionAst"), None)
            if command is None or len(command) != 1 or command[0].tag != "BinaryExpressionAst":
                return False

            bin_expr = command[0]
            if bin_expr[0].tag != "VariableExpressionAst" or bin_expr[1].tag != "ConstantExpressionAst" \
                    or bin_expr[0].attrib["VariablePath"] != "_":
                return False

            arr_literal = next(subnodes[1].iter("ArrayLiteralAst"), None)
            if arr_literal is None:
                return False

            array_values = get_array_literal_values(arr_literal)
            if array_values is None or not all(type(v) is int for v in array_values):
                return False

            match bin_expr.attrib["Operator"]:
                case "Bxor":
                    bin_op = lambda a, b: a ^ b  # noqa:E731
                case "Plus":
                    bin_op = lambda a, b: a + b  # noqa:E731
                case "Minus":
                    bin_op = lambda a, b: a - b  # noqa:E731
                case _:
                    return False

            assert bin_expr[1].text
            array_values_mod = [bin_op(value, int(bin_expr[1].text)) for value in array_values]
            new_array_elem = create_array_literal_values(array_values_mod)

            replace_node(ast, node, new_array_elem, parents=parents)
            return True

    return False
