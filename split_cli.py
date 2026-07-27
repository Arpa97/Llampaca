import sys

with open("llampaca/cli.py", "r") as f:
    lines = f.readlines()

header = lines[0:27]
format_size_lines = lines[27:35]
cli_before_async = lines[35:176]
async_run_chat_lines = lines[176:809]
cli_after_async = lines[809:]

with open("llampaca/cli_utils.py", "w") as f:
    f.writelines(header)
    f.writelines(format_size_lines)

with open("llampaca/cli_chat.py", "w") as f:
    f.writelines(header)
    f.write("from llampaca.cli_utils import format_size\n")
    f.writelines(async_run_chat_lines)

with open("llampaca/cli.py", "w") as f:
    f.writelines(header)
    f.write("from llampaca.cli_utils import format_size\n")
    f.write("from llampaca.cli_chat import async_run_chat\n")
    f.writelines(cli_before_async)
    f.writelines(cli_after_async)

print("Split cli.py successfully!")
