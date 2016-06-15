import atexit;
import os
import re
import subprocess
import sys
import json

from .base import Base

from deoplete.util import charpos2bytepos
from deoplete.util import error
def dub_import_dirs(startdir):
    #Search for dub.json/dub.sdl
    curr_dir = os.path.abspath(startdir)
    found = False
    while curr_dir != "/" and curr_dir != "":
        if os.path.isfile(curr_dir+"/dub.json") or os.path.isfile(curr_dir+"/dub.sdl") or os.path.isfile(curr_dir+"/package.json"):
            found = True
            break
        curr_dir = os.path.normpath(os.path.join(curr_dir, os.pardir))

    if not found:
        return []

    ret = []
    #Gather the import dirs from dub
    process = subprocess.Popen(["dub", "describe", "--annotate", "--data-0", "--data=import-paths"], cwd=curr_dir,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    outs, errs = process.communicate()
    return outs.decode().split('\x00')

class Source(Base):
    def __init__(self, vim):
        Base.__init__(self, vim)

        self.name = 'd'
        self.mark = '[d]'
        self.filetypes = ['d']
        self.input_pattern = r'(?:\b[^\W\d]\w*|[\]\)])(?:\.(?:[^\W\d]\w*)?)*\(?'
        self.rank = 500
        self.class_dict = {
            'c': 'class', # - class name
            'i': 'interface', # - interface name
            's': 'struct', # - struct name
            'u': 'union', # - union name
            'v': 'var', # - variable name
            'm': 'var', # - member variable name
            'k': 'keyword', # - keyword, built-in version, scope statement
            'f': 'function', # - function or method
            'g': 'enum', # - enum name
            'e': 'enum', # - enum member
            'P': 'package', # - package name
            'M': 'module', # - module name
            'a': 'array', # - array
            'A': 'aarray', # - associative array
            'l': 'alias', # - alias name
            't': 'template', # - template name
            'T': 'mixin template', # - mixin template name
        }

        self._dcd_client_binary = self.vim.vars['deoplete#sources#d#dcd_client_binary']
        self._dcd_server_binary = self.vim.vars['deoplete#sources#d#dcd_server_binary']
        self.import_dirs_cache = {}

        #TODO handle dcd-server autostart properly
        #if self.vim.vars['deoplete#sources#d#dcd_server_autostart'] == 1:
        #    process = subprocess.Popen([self.dcd_server_binary()])
        #    atexit.register(lambda: process.kill())

    def get_complete_position(self, context):
        m = re.search(r'\w*$', context['input'])
        return m.start() if m else -1

    def gather_candidates(self, context):
        line = self.vim.current.window.cursor[0]
        column = context['complete_position']

        buf = self.vim.current.buffer
        offset = self.vim.call('line2byte', line) + \
            charpos2bytepos(self.vim.options['encoding'], context['input'][: column], column) - 1
        offset += len(context['complete_str'])
        source = '\n'.join(buf).encode()

        buf_path = os.path.dirname(buf.name);

        args = [self.dcd_client_binary(), "-c" + str(offset)]
        #Use buf_path as import_dirs is a terrible idea
        if buf.name not in self.import_dirs_cache:
            self.import_dirs_cache[buf.name] = dub_import_dirs(os.path.dirname(buf.name))

        args = args+self.import_dirs_cache[buf.name]

        process = subprocess.Popen(args,
                                   stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE,
                                   start_new_session=True)
        process.stdin.write(source)
        stdout_data, stderr_data = process.communicate()
        result = stdout_data.decode().split('\n')

        if stderr_data != b'':
            raise Exception((args, stderr_data.decode()))

        if result[0] == "identifiers":
            return self.identifiers_from_result(result)
        elif result[0] ==  "calltips":
            return self.calltips_from_result(result)

        return []

    def identifiers_from_result(self, result):
        out = []
        sep = ' '

        candidates = []
        longest_class_length = 0
        for complete in result[1:]:
            if complete.strip() == '':
                continue

            pieces = complete.split("\t")
            if len(pieces) < 2:
                raise Exception(pieces)

            # asterisk represents an internal (to DCD) type
            if pieces[1] == "*":
                continue

            candidates.append(pieces)

            class_len = len(self.class_dict[pieces[1]])

            if class_len > longest_class_length:
                longest_class_length = class_len

        for pieces in candidates:
            word = pieces[0]
            _class = self.class_dict[pieces[1]]
            abbr = _class.ljust(longest_class_length + 1) + word
            info = _class

            candidate = dict(word=word,
                              abbr=abbr,
                              info=info,
                              dup=1
                              )

            out.append(candidate)

        return out

    def calltips_from_result(self, result):
        out = []

        result = result[1:]
        for calltip in result:
            candidate = dict(
                abbr=calltip,
                word=self.parse_function_parameters(calltip),
                info=calltip
            )

            out.append(candidate)

        return out

    def parse_function_parameters(self, decl):
        """Parses the function parameters from a function decl, returns them as a string"""
        last_lparen = decl.rfind('(')
        last_rparen = decl.rfind(')')

        param_list = decl[last_lparen + 1 : last_rparen]
        param_list = param_list.split(' ')
        # take only the names
        param_list = param_list[1::2]

        return ' '.join(param_list)

    def dcd_client_binary(self):
        try:
            if os.path.isfile(self._dcd_client_binary):
                return self._dcd_client_binary
            else:
                raise
        except Exception:
            return self.find_binary_path('dcd-client')

    def dcd_server_binary(self):
        try:
            if os.path.isfile(self._dcd_server_binary):
                return self._dcd_server_binary
            else:
                raise
        except Exception:
            return self.find_binary_path('dcd-server')

    def find_binary_path(self, cmd):
        def is_exec(fpath):
            return os.path.isfile(fpath) and os.access(fpath, os.X_OK)

        fpath, fname = os.path.split(cmd)
        if fpath:
            if is_exec(cmd):
                return cmd
        else:
            for path in os.environ["PATH"].split(os.pathsep):
                path = path.strip('"')
                binary = os.path.join(path, cmd)
                if is_exec(binary):
                    return binary
        return error(self.vim, cmd + ' binary not found')

