import dis
import marshal
import sys

def decompile_pyc(file_path):
    with open(file_path, "rb") as f:
        # Skip the pyc header (16 bytes for Python 3.7+)
        f.read(16)
        code_obj = marshal.load(f)
        dis.dis(code_obj)

if __name__ == "__main__":
    decompile_pyc(sys.argv[1])
