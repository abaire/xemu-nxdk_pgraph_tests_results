xemu-nxdk_pgraph_tests_results
===

Output of abaire/nxdk_pgraph_tests on various versions of [xemu](xemu.app)

[Browsable in the wiki](https://github.com/abaire/xemu-nxdk_pgraph_tests_results/wiki/Results)

*Note*: web-display of output may not always match the visible output from the
tests.
In particular, the framebuffer captures in this repository will respect alpha
values in a
way that may not match what is seen within the emulator.

# Updating

Note: Commands below assume that the `requirements.txt` packages have all been
installed.

```shell
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Running tests for a new xemu (or nxdk_pgraph_tests) release

* You will need to provide your own BIOS and MCPX boot images.
* The test procedure can take a very long time (more than 60 minutes).

### Test the latest xemu with the latest nxdk_pgraph_tests

```shell
./execute.py -B <path_to_bios> -M <path_to_mcpx>
```

### Testing against specific xemu and/or nxdk_pgraph_tests

```shell
./execute.py \
  -B <path_to_bios> \
  -M <path_to_mcpx> \
  --xemu-tag v0.8.7 \
  --pgraph-tag v2025-02-04_12-54-35-248456211
```

### Reusing the nxdk_pgraph_tests ISO and/or xemu binary

You can use the `--iso` and `--xemu` flags to specify existing artifacts. This
will skip an automated check against the GitHub API for the `latest` tagged
artifacts.

## Generating diffs

You will need to
install [perceptualdiff](https://github.com/myint/perceptualdiff)

### Compare to the latest [Xbox hardware goldens](https://github.com/abaire/nxdk_pgraph_tests_golden_results)

*Note*: This repository contains a GitHub Action that will perform the hardware comparison on new results after
they are merged to the `main` branch.

```shell
./compare.py <results_directory_created_by_execute>
```

### Compare between xemu versions or host machines

```shell
./compare.py <results_directory_created_by_execute> --against <another_results_directory_created_by_execute>
```

## Submitting new results or comparisons

Use git to create a new branch, add the generated files, and create a pull
request.

```shell
git checkout -b my_new_results
git add .
git commit -m "A message explaining these changes"
git push origin my_new_results
```

Then create a new pull request
on [the GitHub project page](https://github.com/abaire/xemu-nxdk_pgraph_tests_results)
