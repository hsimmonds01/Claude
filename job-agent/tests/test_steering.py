from pathlib import Path

from jobagent.steering import RECENT_REACTIONS_LIMIT, load, parse_feedback

REPO_ROOT = Path(__file__).parent.parent


class TestTheActualShippedTemplates:
    """Against the real files, not fixtures.

    The fixtures all passed while the shipped feedback.md quietly fed its own
    "useful shapes for a line" examples to the model as though she had written
    them -- so on day one the agent believed she'd said "too junior" and
    "wrong kind of <word that keeps matching badly>". Testing the real
    templates is the only thing that catches that.
    """

    def test_the_shipped_templates_contain_no_guidance_yet(self):
        steering = load(REPO_ROOT)

        assert steering.standing_rules == ()
        assert steering.recent_reactions == ()
        assert steering.profile == ""
        assert steering.has_any_guidance is False

    def test_the_shipped_cv_is_the_fictional_one(self):
        # If this ever fails in the build repo, a real CV has been committed
        # to a public repository.
        steering = load(REPO_ROOT)

        assert "FICTIONAL" in steering.cv


class TestUnfilledTemplateDetection:
    def test_headings_and_blank_prompts_do_not_count_as_content(self, tmp_path):
        (tmp_path / "profile.md").write_text(
            "# What I'm looking for\n\n## Job titles\n\n-\n-\n\n> \n\n---\n",
            encoding="utf-8",
        )

        assert load(tmp_path).profile == ""

    def test_one_real_answer_is_enough(self, tmp_path):
        (tmp_path / "profile.md").write_text(
            "## Job titles\n\n- Operations Associate\n", encoding="utf-8"
        )

        steering = load(tmp_path)

        assert "Operations Associate" in steering.profile
        assert steering.has_any_guidance is True

    def test_free_text_prose_counts(self, tmp_path):
        (tmp_path / "profile.md").write_text(
            "## What I actually want\n\n> I want a team where I can learn.\n",
            encoding="utf-8",
        )

        assert load(tmp_path).profile != ""


TWO_SECTION = """
# Steering

## Standing rules

- Never show me anything from a recruitment agency.
- Nothing needing more than 2 days a week in an office.

<!--
Examples of what belongs here:
- Never show me anything under £30,000.
-->

## Recent reactions

- too junior
- more like this one: https://example.com/job/1
"""


class TestTwoSections:
    def test_splits_standing_rules_from_reactions(self):
        standing, reactions = parse_feedback(TWO_SECTION)

        assert standing == (
            "Never show me anything from a recruitment agency.",
            "Nothing needing more than 2 days a week in an office.",
        )
        assert reactions == (
            "too junior",
            "more like this one: https://example.com/job/1",
        )

    def test_commented_out_examples_are_not_treated_as_instructions(self):
        # The blank template ships with examples in HTML comments. If those
        # leaked through, the agent would silently obey rules she never wrote.
        standing, _ = parse_feedback(TWO_SECTION)

        assert not any("£30,000" in rule for rule in standing)

    def test_only_the_newest_reactions_are_kept(self):
        many = "\n".join(f"- reaction {i}" for i in range(40))
        _, reactions = parse_feedback(f"## Recent reactions\n{many}")

        assert len(reactions) == RECENT_REACTIONS_LIMIT
        assert reactions[-1] == "reaction 39"  # newest survives
        assert reactions[0] == f"reaction {40 - RECENT_REACTIONS_LIMIT}"

    def test_a_new_heading_ends_the_section(self):
        text = """
## Standing rules
- keep this

## Something else
- not a rule
"""
        standing, _ = parse_feedback(text)

        assert standing == ("keep this",)


class TestLenience:
    def test_bullets_with_no_headings_are_kept_as_reactions(self):
        # If she ignores the sections and just types, losing her input would
        # make the steering loop look broken. Be lenient.
        _, reactions = parse_feedback("- too junior\n- no agencies\n")

        assert reactions == ("too junior", "no agencies")

    def test_blank_template_yields_nothing(self):
        blank = """
## Standing rules

-
-

## Recent reactions

-
"""
        standing, reactions = parse_feedback(blank)

        assert standing == ()
        assert reactions == ()

    def test_empty_file(self):
        assert parse_feedback("") == ((), ())

    def test_asterisk_bullets_work_too(self):
        standing, _ = parse_feedback("## Standing rules\n* no agencies\n")

        assert standing == ("no agencies",)
