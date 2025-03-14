results
===

The results of the test runner will be placed here.

## known_issues.json

The `known_issues.json` file is used to track information about expected test
failures. This file is not intended to be an exhaustive list of differences
versus Xbox hardware, instead it captures things like misbehavior due to
specific graphics drivers that may cause false positives during regression test
result examination.

Format:

```json
{
  "known_issues": {
    "SUITE_NAME": {
      "issues": [ISSUE_STATEMENT_OBJECT],
      "TEST_NAME": {
        "issues": [ISSUE_STATEMENT_OBJECT]
      }
    }
  }
}

ISSUE_STATEMENT_OBJECT:
{
    "text": "Text describing the known issue that should appear next to the test case name.",
    "filter": {
        "xemu": ["XEMU_VERSION_COMPARATOR"],
        "platform": ["PLATFORM_COMPARATOR"],
        "gl": ["GL_INFO_COMPARATOR"],
        "glsl": ["GLSL_INFO_COMPARATOR"],
        "subfilters": [ADDITIONAL_FILTER_OBJECTS]
    }
}
```

Filters are walked recursively and matched against test result path components
to ignore irrelevant messages. Omission of any value will match all values.

For example, the filter:

```json
{
  "text": "Something didn't work before 0.8.20",
  "xemu": [
    "<0.8.20"
  ]
}
```

Will cause the associated `"text"` to be applied anytime xemu results
with a version less than `0.8.20` are
generated with any `platform/gl/glsl` path.

```json
{
  "text": "After 0.8.20, this test is broken on Darwin/arm64 and all Vulkan implementations",
  "xemu": [
    ">0.8.20"
  ],
  "subfilters": [
    {
      "platform": [
        "Darwin_arm64"
      ]
    },
    {
      "gl": [
        "vk_*"
      ]
    }
  ]
}
```

Will apply `text` to any xemu > version 0.8.20 run on an Apple silicon
macOS or on any platform using Vulkan.

Note: It is valid to omit all the actual filter keys and just include a
`"subfilters"` element, e.g., if the above case did not depend on xemu version.
