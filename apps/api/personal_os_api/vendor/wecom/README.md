# Attribution

`crypto.py` is copied unchanged from `tzwkb/wecom-agent`,
commit `437d69653d1bbbb12e976b3aa78ba464d3d2bb30`,
`decrypt/wxwork_crypto.py`. MIT license is included alongside it.

Upstream: https://github.com/tzwkb/wecom-agent

Only the offline cryptographic primitives are imported. The upstream key-printing
coordinator, automatic directory selection and contact/message export commands are
not executed. WAL validation and snapshot isolation are implemented separately.
