I think it's about time to split pyscv into its own project - let's make it a
separate repo in src with complete history imported and instructions updated
accordingly (we can start counting versions there with 0.1.0

Okay, the purpose is to augment existing pythonic (mainly) dependency scanning
tooling - attestations exist to some extent and can be used but not for all
packages and there's no unified way to get trust root etc for each package
during routine scanning.

How this tool should be used? We have either library or project with
dependencies installed, locked, or downloaded - and we want to 1) ensure we
detect anomalies when upgrading 2) can report attestations etc status on each
dependency 3) can run deeper analysis if project uses github (and in future
other public compatible places) e.g. we can verify signatures on commits and
tags against known keys, can check repo settings, check statuses, etc - way
beyond signature verification on artifact (but first we can and should establish
that the artifact in indeed coming from there) 4) verify that multiple repos
have exactly the same version of the artifact, 5) and probably other advanced
things and also and more importantly when project is shipped to users or library
is distributed we provide a tool to verify that hackers didn't hijack our
account and didn't ship malware (basically the same thing we want to verify
about dependencies on regular basis too but with our project we can be more
decisive about trust root and changes - while for external things we would often
have to rely on tofu and informed user consent)

I think we could collect information on dependencies either right in
pyproject.toml or maybe additional toml file like uv.lock - basically whatever
consistutes current security status of each dep and current trust root / signing keys / etc - github repo and settings are major part of that - things that can be verified from multiple sources when adding packages, basically tofu, but when anything changes user should be able to check before approving (and we could track who approved changes to those values by commit signatures and use codeowners to limit access)

but first we need to finalize the UI part - I want cli that can run in any
project with pyproject.toml and uv.lock and audit dependencies checking cached
artifacts and caches to attestations, detecting github repos, comparing pypi
metadata and filling the toml file with details on each package. In fact I think
we need both per-dep sections in pyproject toml (for trust values and detection overrides) and a separate file with current status of each that can be updated without needing to change the trusted values...

And then we can inform user about improvements and warn about something
decreasing and critical changes e.g. change of github repo should be manually
approved by user before review.

We need to check scorecard etc for how the criteria we should look for in each
project's github to measure health metrics and verify security.

Basically there are many things we could easily check for to analyse source
chain security and score dependencies and provide some summary analysis +
periodic check tool.
