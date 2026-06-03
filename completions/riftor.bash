# bash completion for riftor
# Install: source this file, or copy to /etc/bash_completion.d/riftor
_riftor() {
    local cur prev opts
    COMPREPLY=()
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"
    opts="--version --config --model --api-key --workdir --scope-file --prompt --headless --help"

    case "$prev" in
        --workdir|--scope-file)
            COMPREPLY=( $(compgen -f -- "$cur") )
            return 0
            ;;
        --model)
            COMPREPLY=( $(compgen -W "anthropic/claude-sonnet-4-6 openai/gpt-4o openrouter/auto ollama_chat/llama3.1" -- "$cur") )
            return 0
            ;;
    esac
    COMPREPLY=( $(compgen -W "${opts}" -- "$cur") )
}
complete -F _riftor riftor
