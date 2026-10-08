#!/bin/bash
# PreToolUse Bash: require an explicit output for plutil transformations.
exec /usr/bin/perl -MJSON::PP -MText::ParseWords -e '
local $/;
my $data = eval { decode_json(<STDIN>) };
exit 0 unless ref($data) eq "HASH" && ($data->{tool_name} // "") eq "Bash";
exit 0 unless ref($data->{tool_input}) eq "HASH";
my $cmd = $data->{tool_input}{command};
exit 0 if ref($cmd) || !defined($cmd);
# Split command boundaries while preserving quoted text; never execute input.
my @segments;
my $segment = "";
my $quote = "";
my $escape = 0;
for my $char (split //, $cmd) {
    if ($escape) { $segment .= $char; $escape = 0; next; }
    if ($char eq "\\" && $quote ne "\x27") { $segment .= $char; $escape = 1; next; }
    if ($quote) { $quote = "" if $char eq $quote; $segment .= $char; next; }
    if ($char eq "\x27" || $char eq "\x22") { $quote = $char; $segment .= $char; next; }
    if ($char =~ /[;&|()<>\n]/) { push @segments, $segment; $segment = ""; next; }
    $segment .= $char;
}
exit 0 if $quote || $escape;
push @segments, $segment;
for my $text (@segments) {
    my @args = shellwords($text);
    while (@args) {
        if ($args[0] =~ /^[A-Za-z_][A-Za-z0-9_]*=/) { shift @args; next; }
        last unless $args[0] =~ m{(?:^|/)(sudo|env|nice|command|exec|time|nohup)$};
        my $wrapper = $1;
        shift @args;
        while (@args && $args[0] =~ /^-/) {
            my $option = shift @args;
            last if $option eq "--";
            # Options with a separate operand; flags such as sudo -n and env -i have none.
            shift @args if @args && (
                ($wrapper eq "sudo" && $option =~ /^(?:-[ughpCrT]|--(?:user|group|host|prompt|chdir|chroot|command-timeout))$/)
                || ($wrapper eq "env" && $option =~ /^(?:-[uCS]|--(?:unset|chdir|split-string))$/)
                || ($wrapper eq "nice" && $option =~ /^(?:-n|--adjustment)$/)
                || ($wrapper eq "time" && $option =~ /^(?:-[fo]|--(?:format|output))$/)
                || ($wrapper eq "exec" && $option eq "-a"));
        }
    }
    next unless @args && $args[0] =~ m{(?:^|/)plutil$};
    shift @args;
    my ($edit, $output) = (0, 0);
    for (my $i = 0; $i < @args; $i++) {
        $edit = 1 if $args[$i] =~ /^-(extract|replace|insert|remove|convert)$/;
        $output = 1 if $args[$i] eq "-o" && $i+1 < @args && ($args[$i+1] eq "-" || $args[$i+1] !~ /^-/);
    }
    if ($edit && !$output) {
        print encode_json({hookSpecificOutput => {hookEventName => "PreToolUse", permissionDecision => "deny",
            permissionDecisionReason => "plutil edits/extracts require an explicit -o output path (use -o - for stdout)."}});
        last;
    }
}
'
