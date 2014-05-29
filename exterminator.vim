
python << EOF

import vim
import sys, os, psutil, subprocess, threading, json, time
sys.path.append(os.path.expanduser(vim.eval("g:exterminator_dir")))
import gdb_fancy

def InitRemoteGdbWithFile(exterminator_file):
    while True:
       try:
           data = open(exterminator_file, 'r').read()
           host, port = json.loads(open(exterminator_file, 'r').read())
           break
       except ValueError, IOError:
           time.sleep(0.1)
    InitRemoteGdb(host, port)

def InitRemoteGdb(host, port):
    vim.gdb = gdb_fancy.RemoteGdb(vim, host, port)

def get_loc():
    return vim.current.buffer.name, vim.current.range.start + 1

def get_tok():
    return vim.eval('expand("<cword>")')

def display(data, window_name='mandrews', new_command='bot 15new'):
    winnr = int(vim.eval("winnr()"))
    try:
        while True:
            vim.command("wincmd w")
            if int(vim.eval("exists('b:mandrews_output_window')")) > 0:
                if str(vim.eval("b:mandrews_output_window")) == window_name:
                    break
            if winnr == int(vim.eval("winnr()")):
                vim.command(new_command)
                vim.command("setlocal buftype=nowrite bufhidden=wipe modifiable nobuflisted noswapfile nowrap nonumber")
                vim.command("let b:mandrews_output_window='%s'" % window_name)
                break
        vim.current.window.buffer[:] = data.split('\n')
        vim.command("setlocal nomodifiable")
    finally:
        vim.command("%swincmd w" % winnr)

def HandleEvents():
    try:
        vim.gdb.handle_events()
    except:
        pass

EOF

function! StartDebugger(...)
    Rooter
    let g:exterminator_file = substitute(system('mktemp'), '\n$', '', '')
    exec 'silent ! tmux split -p 30 -h "VIM_SERVER=' . v:servername . ' EXTERMINATOR_FILE=' . g:exterminator_file . ' ' . g:exterminator_dir . '/dbg '. join(a:000, ' ') . '"'
    if len(v:servername) == 0
        python InitRemoteGdbFromFile(vim.eval("g:exterminator_file"))
    endif
    unlet g:exterminator_file
    wincmd =
endfunction

if exists('g:exterminator_file')
    python InitRemoteGdb(vim.eval("g:exterminator_file"))
    unlet g:exterminator_file
endif

comm! -nargs=0 GdbToggle    python vim.gdb.toggle_break(*get_loc())
comm! -nargs=0 GdbContinue  python vim.gdb.send_continue()
comm! -nargs=0 GdbNext      python vim.gdb.send_next()
comm! -nargs=0 GdbStep      python vim.gdb.send_step()
comm! -nargs=0 GdbEval      python vim.gdb.eval_expr(get_tok())
comm! -nargs=+ Dbg         call StartDebugger(<f-args>)

highlight SignColumn guibg=Black guifg=White ctermbg=None ctermfg=White

sign define breakpoint text=>> texthl=Comment
sign define just_pc text=-- texthl=Debug
sign define pc_and_breakpoint text=-> texthl=Debug

au CursorHold *             python HandleEvents()
