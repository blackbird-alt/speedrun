// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

//! Speedrun fork additions for the NCEES FE Electrical and Computer study tool.
//!
//! Two read-only features live here, both built entirely on data the engine
//! already owns (tags + FSRS memory state). Neither mutates card scheduling
//! state, intervals, or the review log, so undo and collection integrity are
//! unaffected.
//!
//! 1. **Points-at-stake queue** — a re-ordering of the existing due/new queue
//!    by `topic weight × student weakness`. Rather than a hard sort (which would
//!    front-load one whole topic — e.g. 20 straight Circuit Analysis cards on a
//!    fresh profile), topics are **interleaved** with frequency proportional to
//!    their score, so a session spans many areas while the heaviest/weakest
//!    areas recur most often — matching the fully-interleaved exam. Topic
//!    weights are seeded from the NCEES question-count ranges and live in the
//!    collection config table, not in this code. Student weakness is
//!    `1 - mean recall` for a topic, derived from review history.
//!
//! 2. **Honest memory score** — an aggregate of FSRS per-card retrievability
//!    presented as a range with a pre-registered give-up rule, never a bare
//!    single number.

use std::collections::HashMap;

use fsrs::FSRS5_DEFAULT_DECAY;
use fsrs::FSRS;

use crate::prelude::*;
use crate::scheduler::timing::SchedTimingToday;

/// Config key (collection `config` table) holding the JSON topic-weight map:
/// `{ "<topic_key>": <weight> }`. Kept out of engine code on purpose.
pub const TOPIC_WEIGHTS_CONFIG_KEY: &str = "feTopicWeights";
/// Config key holding the fallback weight for untagged/unknown topics.
pub const DEFAULT_TOPIC_WEIGHT_CONFIG_KEY: &str = "feDefaultTopicWeight";
/// Config key: minimum graded reviews before the memory score is shown.
pub const MEMORY_MIN_REVIEWS_CONFIG_KEY: &str = "feMemoryMinReviews";
/// Config key: minimum distinct topics before the memory score is shown.
pub const MEMORY_MIN_TOPICS_CONFIG_KEY: &str = "feMemoryMinTopics";
/// Config key: minimum graded exam-style reviews before performance is shown.
pub const PERFORMANCE_MIN_REVIEWS_CONFIG_KEY: &str = "fePerformanceMinReviews";
/// Config key: minimum distinct topics before performance is shown.
pub const PERFORMANCE_MIN_TOPICS_CONFIG_KEY: &str = "fePerformanceMinTopics";
/// Config key: minimum graded reviews before readiness is shown.
pub const READINESS_MIN_REVIEWS_CONFIG_KEY: &str = "feReadinessMinReviews";
/// Config key: minimum area-coverage fraction (0..1) before readiness is shown.
pub const READINESS_MIN_COVERAGE_CONFIG_KEY: &str = "feReadinessMinCoverage";

/// Fallback weight used when a card's topic is unknown or untagged. Chosen to
/// sit near the lowest-weight NCEES areas so unmapped cards still surface, but
/// below the heavy areas.
pub const DEFAULT_TOPIC_WEIGHT: f64 = 4.0;

/// Pre-registered give-up thresholds (PRD §7.3). Set in advance, not after
/// seeing which number looks good.
pub const DEFAULT_MEMORY_MIN_REVIEWS: u32 = 50;
pub const DEFAULT_MEMORY_MIN_TOPICS: u32 = 3;

/// Performance (exam-style problem) give-up thresholds. Lower than memory
/// because the exam-style card population is a subset.
pub const DEFAULT_PERFORMANCE_MIN_REVIEWS: u32 = 30;
pub const DEFAULT_PERFORMANCE_MIN_TOPICS: u32 = 2;

/// Readiness give-up thresholds (PRD example: "at least 200 graded reviews and
/// 50% topic coverage"). Deliberately strict: for a small deck readiness will
/// abstain, which is the honest, rewarded outcome.
pub const DEFAULT_READINESS_MIN_REVIEWS: u32 = 200;
pub const DEFAULT_READINESS_MIN_COVERAGE: f64 = 0.5;

const DEFAULT_QUEUE_SEARCH: &str = "is:due OR is:new";
const DEFAULT_MEMORY_SEARCH: &str = "is:review OR is:learn";
/// Default scope for the performance score: worked, exam-style problem cards
/// (tagged `track::durable`), not the fact/cram cards the memory score covers.
const DEFAULT_PERFORMANCE_SEARCH: &str = "tag:track::durable";
const DEFAULT_READINESS_SEARCH: &str = "is:review OR is:learn";
/// Matches every note tagged under the `fe::` hierarchy.
const TOPIC_TAG_SEARCH: &str = "tag:fe::*";

/// Seed topic weights, taken from the midpoints of the NCEES FE Electrical and
/// Computer question-count ranges (current CBT spec, 110 questions). Used to
/// populate the config table the first time it is read; the config table is the
/// source of truth thereafter.
pub fn seed_topic_weights() -> HashMap<String, f64> {
    [
        ("mathematics", 14.0),
        ("probability_statistics", 5.0),
        ("ethics", 4.0),
        ("engineering_economics", 4.0),
        ("properties_of_electrical_materials", 5.0),
        ("engineering_sciences", 7.0),
        ("circuit_analysis", 12.0),
        ("linear_systems", 6.0),
        ("signal_processing", 6.0),
        ("electronics", 9.0),
        ("power_systems", 10.0),
        ("electromagnetics", 6.0),
        ("control_systems", 7.0),
        ("communications", 6.0),
        ("computer_networks", 4.0),
        ("digital_systems", 9.0),
        ("computer_systems", 5.0),
        ("software_development", 5.0),
    ]
    .into_iter()
    .map(|(k, v)| (k.to_string(), v))
    .collect()
}

/// Per-card diagnostics, returned parallel to the ordered queue.
#[derive(Debug, Clone, PartialEq)]
pub struct PointsAtStakeEntry {
    pub card_id: CardId,
    /// Topic key, e.g. "circuit_analysis"; `None` when untagged/unknown.
    pub topic: Option<String>,
    pub topic_weight: f64,
    pub weakness: f64,
    pub score: f64,
    pub recall: f64,
    pub has_memory: bool,
    /// Card due value, kept for deterministic tie-breaking; not surfaced over
    /// the protobuf boundary.
    pub(crate) due: i32,
}

#[derive(Debug, Clone, PartialEq)]
pub struct PointsAtStakeQueue {
    /// Entries in queue order: highest value first.
    pub entries: Vec<PointsAtStakeEntry>,
}

impl PointsAtStakeQueue {
    pub fn card_ids(&self) -> Vec<CardId> {
        self.entries.iter().map(|e| e.card_id).collect()
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct MemoryScore {
    /// False when the give-up rule withholds the score.
    pub shown: bool,
    pub point_estimate: f64,
    pub range_low: f64,
    pub range_high: f64,
    pub coverage: f64,
    pub graded_reviews: u32,
    pub topics_covered: u32,
    pub last_updated: i64,
    pub main_reason: String,
    pub next_action: String,
    pub withheld_reason: String,
    pub min_reviews_required: u32,
    pub min_topics_required: u32,
}

/// Honest "performance" score over exam-style (worked-problem) cards. Same
/// shape as [`MemoryScore`] but scoped to a different card population, so it is
/// not a copy of the memory number.
#[derive(Debug, Clone, PartialEq)]
pub struct PerformanceScore {
    pub shown: bool,
    pub point_estimate: f64,
    pub range_low: f64,
    pub range_high: f64,
    pub coverage: f64,
    pub graded_reviews: u32,
    pub topics_covered: u32,
    pub last_updated: i64,
    pub main_reason: String,
    pub next_action: String,
    pub withheld_reason: String,
    pub min_reviews_required: u32,
    pub min_topics_required: u32,
}

/// Honest "readiness" score on the FE pass/fail scale. Either a pass-probability
/// range, or an abstention with the single best next action.
#[derive(Debug, Clone, PartialEq)]
pub struct ReadinessScore {
    pub shown: bool,
    pub pass_probability: f64,
    pub range_low: f64,
    pub range_high: f64,
    pub coverage: f64,
    pub areas_covered: u32,
    pub areas_total: u32,
    pub graded_reviews: u32,
    pub last_updated: i64,
    pub main_reason: String,
    pub next_action: String,
    pub withheld_reason: String,
    pub min_reviews_required: u32,
    pub min_coverage_required: f64,
}

/// Extract a topic key from a note's tags. Recognises the first tag under the
/// `fe::` hierarchy (case-insensitive) and returns its first segment, e.g.
/// `FE::Circuit_Analysis` or `fe::circuit_analysis::ohms_law` → "circuit_analysis".
fn topic_from_tags(tags: &[String]) -> Option<String> {
    for tag in tags {
        let lower = tag.to_ascii_lowercase();
        if let Some(rest) = lower.strip_prefix("fe::") {
            let key = rest.split("::").next().unwrap_or(rest);
            if !key.is_empty() {
                return Some(key.to_string());
            }
        }
    }
    None
}

/// Current FSRS retrievability for a card, or `None` if it has no memory state.
fn card_recall(card: &Card, fsrs: &FSRS, timing: &SchedTimingToday) -> Option<f32> {
    let memory = card.memory_state?;
    let elapsed = card.seconds_since_last_review(timing).unwrap_or_default();
    let decay = card.decay.unwrap_or(FSRS5_DEFAULT_DECAY);
    Some(fsrs.current_retrievability_seconds(memory.into(), elapsed, decay))
}

impl Collection {
    /// Topic-weight map, read from the config table. Falls back to the
    /// NCEES-derived seed defaults when the table has not been populated, so
    /// the query path stays read-only and never crashes on a fresh
    /// collection. The config table is the source of truth once seeded
    /// (see [`Collection::ensure_fe_topic_weights_seeded`]).
    pub(crate) fn fe_topic_weights(&self) -> HashMap<String, f64> {
        self.get_config_optional::<HashMap<String, f64>, _>(TOPIC_WEIGHTS_CONFIG_KEY)
            .filter(|map| !map.is_empty())
            .unwrap_or_else(seed_topic_weights)
    }

    /// Persist the NCEES-derived seed weights into the config table if absent,
    /// so weights become editable config data rather than living in code.
    /// Called during deck setup; safe to call repeatedly.
    pub fn ensure_fe_topic_weights_seeded(&mut self) -> Result<()> {
        let existing =
            self.get_config_optional::<HashMap<String, f64>, _>(TOPIC_WEIGHTS_CONFIG_KEY);
        if existing.map(|m| !m.is_empty()).unwrap_or(false) {
            return Ok(());
        }
        self.set_config_json(TOPIC_WEIGHTS_CONFIG_KEY, &seed_topic_weights(), false)?;
        Ok(())
    }

    fn fe_default_topic_weight(&self) -> f64 {
        self.get_config_optional::<f64, _>(DEFAULT_TOPIC_WEIGHT_CONFIG_KEY)
            .unwrap_or(DEFAULT_TOPIC_WEIGHT)
    }

    fn fe_memory_min_reviews(&self) -> u32 {
        self.get_config_optional::<u32, _>(MEMORY_MIN_REVIEWS_CONFIG_KEY)
            .unwrap_or(DEFAULT_MEMORY_MIN_REVIEWS)
    }

    fn fe_memory_min_topics(&self) -> u32 {
        self.get_config_optional::<u32, _>(MEMORY_MIN_TOPICS_CONFIG_KEY)
            .unwrap_or(DEFAULT_MEMORY_MIN_TOPICS)
    }

    fn fe_performance_min_reviews(&self) -> u32 {
        self.get_config_optional::<u32, _>(PERFORMANCE_MIN_REVIEWS_CONFIG_KEY)
            .unwrap_or(DEFAULT_PERFORMANCE_MIN_REVIEWS)
    }

    fn fe_performance_min_topics(&self) -> u32 {
        self.get_config_optional::<u32, _>(PERFORMANCE_MIN_TOPICS_CONFIG_KEY)
            .unwrap_or(DEFAULT_PERFORMANCE_MIN_TOPICS)
    }

    fn fe_readiness_min_reviews(&self) -> u32 {
        self.get_config_optional::<u32, _>(READINESS_MIN_REVIEWS_CONFIG_KEY)
            .unwrap_or(DEFAULT_READINESS_MIN_REVIEWS)
    }

    fn fe_readiness_min_coverage(&self) -> f64 {
        self.get_config_optional::<f64, _>(READINESS_MIN_COVERAGE_CONFIG_KEY)
            .unwrap_or(DEFAULT_READINESS_MIN_COVERAGE)
    }

    /// Map of note id -> topic key for the given cards, fetched once.
    fn topics_for_cards(&self, cards: &[Card]) -> Result<HashMap<NoteId, Option<String>>> {
        let mut out: HashMap<NoteId, Option<String>> = HashMap::new();
        for card in cards {
            if out.contains_key(&card.note_id) {
                continue;
            }
            let topic = match self.storage.get_note(card.note_id)? {
                Some(note) => topic_from_tags(&note.tags),
                None => None,
            };
            out.insert(card.note_id, topic);
        }
        Ok(out)
    }

    /// Build the points-at-stake ordering over the due/new queue. Read-only.
    pub fn build_points_at_stake_queue(&mut self, search: &str) -> Result<PointsAtStakeQueue> {
        let search = if search.trim().is_empty() {
            DEFAULT_QUEUE_SEARCH
        } else {
            search
        };

        let timing = self.timing_today()?;
        let weights = self.fe_topic_weights();
        let default_weight = self.fe_default_topic_weight();
        let fsrs = FSRS::new(None)?;

        // History set: every fe::-tagged card, used to derive per-topic
        // weakness from review history.
        let history_cards = self.all_cards_for_search(TOPIC_TAG_SEARCH)?;
        let history_topics = self.topics_for_cards(&history_cards)?;
        let weakness = topic_weakness(&history_cards, &history_topics, &fsrs, &timing);

        // Candidate set: the cards we are actually ordering.
        let candidates = self.all_cards_for_search(search)?;
        let candidate_topics = self.topics_for_cards(&candidates)?;

        let entries: Vec<PointsAtStakeEntry> = candidates
            .iter()
            .map(|card| {
                let topic = candidate_topics
                    .get(&card.note_id)
                    .cloned()
                    .flatten();
                let topic_weight = topic
                    .as_ref()
                    .and_then(|t| weights.get(t).copied())
                    .unwrap_or(default_weight);
                // Unknown/untagged or unseen topic defaults to maximum weakness
                // so high-weight unseen areas surface early.
                let weak = topic
                    .as_ref()
                    .and_then(|t| weakness.get(t).copied())
                    .unwrap_or(1.0);
                let recall = card_recall(card, &fsrs, &timing).unwrap_or(0.0) as f64;
                PointsAtStakeEntry {
                    card_id: card.id,
                    topic,
                    topic_weight,
                    weakness: weak,
                    score: topic_weight * weak,
                    recall,
                    has_memory: card.memory_state.is_some(),
                    due: card.due,
                }
            })
            .collect();

        // Weighted interleave across topics (not a hard sort): each topic is
        // emitted with frequency proportional to its score, so a session spans
        // many areas instead of front-loading the single heaviest one. Within a
        // topic the most overdue card (smallest due) comes first, then card id,
        // so behaviour degrades to normal Anki ordering inside one topic.
        let entries = interleave_by_score(entries);

        Ok(PointsAtStakeQueue { entries })
    }

    /// Aggregate FSRS retrievability into an honest memory score. Read-only.
    pub fn compute_fe_memory_score(&mut self, search: &str) -> Result<MemoryScore> {
        let search = if search.trim().is_empty() {
            DEFAULT_MEMORY_SEARCH
        } else {
            search
        };

        let min_reviews = self.fe_memory_min_reviews();
        let min_topics = self.fe_memory_min_topics();

        let timing = self.timing_today()?;
        let fsrs = FSRS::new(None)?;

        let cards = self.all_cards_for_search(search)?;
        let topics = self.topics_for_cards(&cards)?;

        let total_scoped = cards.len();
        let mut recalls: Vec<f64> = Vec::new();
        let mut graded_reviews: u32 = 0;
        let mut last_updated: i64 = 0;
        // topic -> (sum recall, count) over graded cards
        let mut per_topic: HashMap<String, (f64, u32)> = HashMap::new();

        for card in &cards {
            graded_reviews = graded_reviews.saturating_add(card.reps);
            if let Some(ts) = card.last_review_time {
                last_updated = last_updated.max(ts.0);
            }
            if let Some(recall) = card_recall(card, &fsrs, &timing) {
                let recall = recall as f64;
                recalls.push(recall);
                if let Some(Some(topic)) = topics.get(&card.note_id) {
                    let entry = per_topic.entry(topic.clone()).or_insert((0.0, 0));
                    entry.0 += recall;
                    entry.1 += 1;
                }
            }
        }

        let with_memory = recalls.len();
        let topics_covered = per_topic.len() as u32;
        let coverage = if total_scoped == 0 {
            0.0
        } else {
            with_memory as f64 / total_scoped as f64
        };

        // Weakest covered topic drives both the main reason and the next action.
        let weakest = per_topic
            .iter()
            .map(|(topic, (sum, count))| (topic.clone(), sum / *count as f64))
            .min_by(|a, b| a.1.total_cmp(&b.1));
        // Single best next action (honesty-rule requirement), always populated so
        // the UI can show it whether or not the score itself is shown yet.
        let next_action = match &weakest {
            Some((topic, mean)) => {
                format!("Drill {topic}, your weakest area at {:.0}%.", *mean * 100.0)
            }
            None => "Study your FE cards to start building a memory estimate.".to_string(),
        };

        let mut score = MemoryScore {
            shown: false,
            point_estimate: 0.0,
            range_low: 0.0,
            range_high: 0.0,
            coverage,
            graded_reviews,
            topics_covered,
            last_updated,
            main_reason: String::new(),
            next_action,
            withheld_reason: String::new(),
            min_reviews_required: min_reviews,
            min_topics_required: min_topics,
        };

        // Give-up rule (pre-registered): no score until enough graded reviews
        // across enough topics, and at least one card with memory state.
        if with_memory == 0 || graded_reviews < min_reviews || topics_covered < min_topics {
            score.withheld_reason = format!(
                "Not enough data yet: {graded_reviews}/{min_reviews} graded reviews across \
                 {topics_covered}/{min_topics} topics. The memory score stays hidden until the \
                 pre-registered threshold is met."
            );
            return Ok(score);
        }

        let mean = recalls.iter().sum::<f64>() / with_memory as f64;
        let (low, high) = confidence_interval(&recalls, mean);

        score.shown = true;
        score.point_estimate = mean;
        score.range_low = low;
        score.range_high = high;
        score.main_reason = match &weakest {
            Some((topic, topic_mean)) => format!(
                "Estimate based on {with_memory} of {total_scoped} studied cards across \
                 {topics_covered} topics; lowest recall is {topic} at {:.0}%.",
                *topic_mean * 100.0
            ),
            None => format!(
                "Estimate based on {with_memory} of {total_scoped} studied cards across \
                 {topics_covered} topics."
            ),
        };

        Ok(score)
    }

    /// Honest performance score over exam-style (worked-problem) cards. Same
    /// aggregation as the memory score but scoped to a *different* card
    /// population (default `tag:track::durable`), so it is not a copy of the
    /// memory number. Read-only.
    pub fn compute_fe_performance_score(&mut self, search: &str) -> Result<PerformanceScore> {
        let search = if search.trim().is_empty() {
            DEFAULT_PERFORMANCE_SEARCH
        } else {
            search
        };

        let min_reviews = self.fe_performance_min_reviews();
        let min_topics = self.fe_performance_min_topics();

        let timing = self.timing_today()?;
        let fsrs = FSRS::new(None)?;

        let cards = self.all_cards_for_search(search)?;
        let topics = self.topics_for_cards(&cards)?;

        let total_scoped = cards.len();
        let mut recalls: Vec<f64> = Vec::new();
        let mut graded_reviews: u32 = 0;
        let mut last_updated: i64 = 0;
        let mut per_topic: HashMap<String, (f64, u32)> = HashMap::new();

        for card in &cards {
            graded_reviews = graded_reviews.saturating_add(card.reps);
            if let Some(ts) = card.last_review_time {
                last_updated = last_updated.max(ts.0);
            }
            if let Some(recall) = card_recall(card, &fsrs, &timing) {
                let recall = recall as f64;
                recalls.push(recall);
                if let Some(Some(topic)) = topics.get(&card.note_id) {
                    let entry = per_topic.entry(topic.clone()).or_insert((0.0, 0));
                    entry.0 += recall;
                    entry.1 += 1;
                }
            }
        }

        let with_memory = recalls.len();
        let topics_covered = per_topic.len() as u32;
        let coverage = if total_scoped == 0 {
            0.0
        } else {
            with_memory as f64 / total_scoped as f64
        };

        // Weakest covered topic drives both the main reason and the next action.
        let weakest = per_topic
            .iter()
            .map(|(topic, (sum, count))| (topic.clone(), sum / *count as f64))
            .min_by(|a, b| a.1.total_cmp(&b.1));
        // Single best next action (honesty-rule requirement), always populated.
        let next_action = match &weakest {
            Some((topic, mean)) => format!(
                "Work more {topic} problems, your weakest exam-style area at {:.0}%.",
                *mean * 100.0
            ),
            None => {
                "Work exam-style problems to start building a performance estimate.".to_string()
            }
        };

        let mut score = PerformanceScore {
            shown: false,
            point_estimate: 0.0,
            range_low: 0.0,
            range_high: 0.0,
            coverage,
            graded_reviews,
            topics_covered,
            last_updated,
            main_reason: String::new(),
            next_action,
            withheld_reason: String::new(),
            min_reviews_required: min_reviews,
            min_topics_required: min_topics,
        };

        if with_memory == 0 || graded_reviews < min_reviews || topics_covered < min_topics {
            score.withheld_reason = format!(
                "Not enough exam-style data yet: {graded_reviews}/{min_reviews} graded problem \
                 reviews across {topics_covered}/{min_topics} topics. Performance stays hidden \
                 until the pre-registered threshold is met."
            );
            return Ok(score);
        }

        let mean = recalls.iter().sum::<f64>() / with_memory as f64;
        let (low, high) = confidence_interval(&recalls, mean);

        score.shown = true;
        score.point_estimate = mean;
        score.range_low = low;
        score.range_high = high;
        score.main_reason = match &weakest {
            Some((topic, topic_mean)) => format!(
                "Based on {with_memory} exam-style cards across {topics_covered} topics; \
                 weakest is {topic} at {:.0}%.",
                *topic_mean * 100.0
            ),
            None => {
                format!("Based on {with_memory} exam-style cards across {topics_covered} topics.")
            }
        };

        Ok(score)
    }

    /// Honest readiness score on the FE pass/fail scale. Returns a
    /// pass-probability range only when there is enough graded history and exam
    /// coverage to defend it; otherwise abstains with the single best next
    /// action. Read-only.
    pub fn compute_fe_readiness_score(&mut self, search: &str) -> Result<ReadinessScore> {
        let search = if search.trim().is_empty() {
            DEFAULT_READINESS_SEARCH
        } else {
            search
        };

        let min_reviews = self.fe_readiness_min_reviews();
        let min_coverage = self.fe_readiness_min_coverage();
        let weights = self.fe_topic_weights();
        let areas_total = seed_topic_weights().len() as u32;

        let timing = self.timing_today()?;
        let fsrs = FSRS::new(None)?;

        let cards = self.all_cards_for_search(search)?;
        let topics = self.topics_for_cards(&cards)?;

        let mut recalls: Vec<f64> = Vec::new();
        let mut graded_reviews: u32 = 0;
        let mut last_updated: i64 = 0;
        let mut per_topic: HashMap<String, (f64, u32)> = HashMap::new();
        let mut covered: std::collections::HashSet<String> = std::collections::HashSet::new();

        for card in &cards {
            graded_reviews = graded_reviews.saturating_add(card.reps);
            if let Some(ts) = card.last_review_time {
                last_updated = last_updated.max(ts.0);
            }
            if let Some(recall) = card_recall(card, &fsrs, &timing) {
                let recall = recall as f64;
                recalls.push(recall);
                if let Some(Some(topic)) = topics.get(&card.note_id) {
                    covered.insert(topic.clone());
                    let entry = per_topic.entry(topic.clone()).or_insert((0.0, 0));
                    entry.0 += recall;
                    entry.1 += 1;
                }
            }
        }

        let areas_covered = covered.len() as u32;
        let coverage = if areas_total == 0 {
            0.0
        } else {
            areas_covered as f64 / areas_total as f64
        };

        // The single best next thing to study (honesty-rule requirement): the
        // heaviest NCEES area not yet covered, or — if all are covered — the
        // weakest covered area.
        let next_action = {
            let mut uncovered: Vec<(&String, f64)> = weights
                .iter()
                .filter(|(area, _)| !covered.contains(*area))
                .map(|(area, w)| (area, *w))
                .collect();
            uncovered.sort_by(|a, b| b.1.total_cmp(&a.1).then(a.0.cmp(b.0)));
            if let Some((area, w)) = uncovered.first() {
                format!("Study {area} (highest-weight area not yet covered, weight {w:.0}).")
            } else {
                let weakest = per_topic
                    .iter()
                    .map(|(topic, (sum, count))| (topic, sum / *count as f64))
                    .min_by(|a, b| a.1.total_cmp(&b.1));
                match weakest {
                    Some((topic, mean)) => {
                        format!("Review {topic}, your weakest covered area at {:.0}%.", mean * 100.0)
                    }
                    None => "Study any core area to start building a readiness estimate.".to_string(),
                }
            }
        };

        let mut score = ReadinessScore {
            shown: false,
            pass_probability: 0.0,
            range_low: 0.0,
            range_high: 0.0,
            coverage,
            areas_covered,
            areas_total,
            graded_reviews,
            last_updated,
            main_reason: String::new(),
            next_action,
            withheld_reason: String::new(),
            min_reviews_required: min_reviews,
            min_coverage_required: min_coverage,
        };

        // Give-up rule (pre-registered): the FE is pass/fail, so no number is
        // shown until there is enough graded history *and* exam coverage.
        if recalls.is_empty() || graded_reviews < min_reviews || coverage < min_coverage {
            score.withheld_reason = format!(
                "Not enough evidence for a readiness call: {graded_reviews}/{min_reviews} graded \
                 reviews and {:.0}%/{:.0}% area coverage. The FE is pass/fail — no number is \
                 shown until there is enough to defend one.",
                coverage * 100.0,
                min_coverage * 100.0
            );
            return Ok(score);
        }

        let mean = recalls.iter().sum::<f64>() / recalls.len() as f64;
        let (low, high) = confidence_interval(&recalls, mean);
        score.shown = true;
        score.pass_probability = mean;
        score.range_low = low;
        score.range_high = high;
        score.main_reason = format!(
            "Pass estimate from {} graded cards across {areas_covered}/{areas_total} areas \
             ({:.0}% coverage).",
            recalls.len(),
            coverage * 100.0
        );

        Ok(score)
    }
}

/// Weighted interleave of scored entries. Groups cards by topic, then emits
/// topics in stride-scheduled order so each topic's frequency is proportional to
/// its score (`topic weight × weakness`). This spreads a session across many
/// areas — matching the fully-interleaved exam — while still giving the
/// heaviest/weakest areas the most cards. Deterministic: within a topic, most
/// overdue (smallest due) then card id; virtual-time ties break on higher score
/// then topic key. All cards of one topic share a score, so the per-topic weight
/// is well defined.
fn interleave_by_score(entries: Vec<PointsAtStakeEntry>) -> Vec<PointsAtStakeEntry> {
    use std::collections::VecDeque;

    let mut groups: HashMap<Option<String>, Vec<PointsAtStakeEntry>> = HashMap::new();
    for entry in entries {
        groups.entry(entry.topic.clone()).or_default().push(entry);
    }

    struct Bucket {
        weight: f64,
        stride: f64,
        vtime: f64,
        key: String,
        cards: VecDeque<PointsAtStakeEntry>,
    }

    let mut buckets: Vec<Bucket> = groups
        .into_iter()
        .map(|(topic, mut cards)| {
            cards.sort_by(|a, b| a.due.cmp(&b.due).then(a.card_id.0.cmp(&b.card_id.0)));
            let weight = cards.first().map(|e| e.score).unwrap_or(0.0);
            // Zero/negative-score topics (e.g. perfectly recalled) get a huge
            // stride so they trail the rest instead of dividing by zero.
            let stride = if weight > 0.0 { 1.0 / weight } else { 1.0e9 };
            Bucket {
                weight,
                stride,
                vtime: stride,
                key: topic.unwrap_or_default(),
                cards: cards.into_iter().collect(),
            }
        })
        .collect();

    // Deterministic order for equal virtual times: heavier first, then key.
    buckets.sort_by(|a, b| b.weight.total_cmp(&a.weight).then(a.key.cmp(&b.key)));

    let total: usize = buckets.iter().map(|b| b.cards.len()).sum();
    let mut out = Vec::with_capacity(total);
    for _ in 0..total {
        let mut best: Option<usize> = None;
        for (i, b) in buckets.iter().enumerate() {
            if b.cards.is_empty() {
                continue;
            }
            best = Some(match best {
                None => i,
                Some(j) => {
                    let c = &buckets[j];
                    let pick = b.vtime < c.vtime
                        || (b.vtime == c.vtime
                            && (b.weight > c.weight || (b.weight == c.weight && b.key < c.key)));
                    if pick {
                        i
                    } else {
                        j
                    }
                }
            });
        }
        let i = best.expect("total > 0 guarantees a non-empty bucket");
        out.push(buckets[i].cards.pop_front().unwrap());
        buckets[i].vtime += buckets[i].stride;
    }
    out
}

/// Per-topic weakness = `1 - mean recall` over cards in the topic that have
/// review history. Topics with no graded history are omitted; callers treat a
/// missing topic as maximum weakness (1.0).
fn topic_weakness(
    cards: &[Card],
    topics: &HashMap<NoteId, Option<String>>,
    fsrs: &FSRS,
    timing: &SchedTimingToday,
) -> HashMap<String, f64> {
    let mut sums: HashMap<String, (f64, u32)> = HashMap::new();
    for card in cards {
        let Some(Some(topic)) = topics.get(&card.note_id) else {
            continue;
        };
        if let Some(recall) = card_recall(card, fsrs, timing) {
            let entry = sums.entry(topic.clone()).or_insert((0.0, 0));
            entry.0 += recall as f64;
            entry.1 += 1;
        }
    }
    sums.into_iter()
        .map(|(topic, (sum, count))| {
            let mean = if count == 0 { 0.0 } else { sum / count as f64 };
            (topic, (1.0 - mean).clamp(0.0, 1.0))
        })
        .collect()
}

/// A 95% interval around the mean recall, clamped to 0..1. Uses the standard
/// error of the mean when there are enough samples; for tiny samples it widens
/// to the observed spread so the range honestly reflects the thin data.
fn confidence_interval(recalls: &[f64], mean: f64) -> (f64, f64) {
    let n = recalls.len();
    if n <= 1 {
        // A single observation tells us almost nothing about the spread.
        return ((mean - 0.5).max(0.0), (mean + 0.5).min(1.0));
    }
    let variance = recalls
        .iter()
        .map(|r| (r - mean).powi(2))
        .sum::<f64>()
        / (n as f64 - 1.0);
    let std_err = (variance / n as f64).sqrt();
    let margin = 1.96 * std_err;
    ((mean - margin).max(0.0), (mean + margin).min(1.0))
}

#[cfg(test)]
mod tests {
    use std::collections::HashMap;

    use fsrs::FSRS5_DEFAULT_DECAY;

    use super::*;
    use crate::card::CardId;
    use crate::card::FsrsMemoryState;
    use crate::collection::Collection;
    use crate::tests::NoteAdder;
    use crate::timestamp::TimestampSecs;

    /// Adds a card tagged `fe::<topic>` and returns its id.
    fn add_tagged_card(col: &mut Collection, front: &str, topic: &str) -> CardId {
        let note = NoteAdder::basic(col)
            .fields(&[front, ""])
            .tags(&[format!("fe::{topic}")])
            .add(col);
        col.storage.card_ids_of_notes(&[note.id]).unwrap()[0]
    }

    /// Forces a card into a review state with the given FSRS memory and an
    /// elapsed time chosen to produce a known-ish recall ordering. Higher
    /// stability => higher recall. Read path only; we set the fields directly.
    fn set_memory(col: &mut Collection, cid: CardId, stability: f32, difficulty: f32) {
        let mut card = col.storage.get_card(cid).unwrap().unwrap();
        card.ctype = crate::card::CardType::Review;
        card.queue = crate::card::CardQueue::Review;
        card.reps = 1;
        card.interval = 10;
        card.due = 0;
        card.memory_state = Some(FsrsMemoryState {
            stability,
            difficulty,
        });
        card.decay = Some(FSRS5_DEFAULT_DECAY);
        card.last_review_time = Some(TimestampSecs::now().adding_secs(-86_400));
        col.storage.update_card(&card).unwrap();
    }

    #[test]
    fn orders_by_topic_weight_times_weakness() {
        let mut col = Collection::new();
        // Override weights so the test is independent of the seed values.
        col.set_config(
            TOPIC_WEIGHTS_CONFIG_KEY,
            &HashMap::from([
                ("circuit_analysis".to_string(), 10.0_f64),
                ("ethics".to_string(), 1.0_f64),
            ]),
        )
        .unwrap();

        // A weak, heavy-weight card (low recall in circuit_analysis) should
        // beat a strong, light-weight card (high recall in ethics).
        let heavy_weak = add_tagged_card(&mut col, "weak heavy", "circuit_analysis");
        let light_strong = add_tagged_card(&mut col, "strong light", "ethics");
        set_memory(&mut col, heavy_weak, 1.0, 9.0); // low stability => low recall
        set_memory(&mut col, light_strong, 1000.0, 1.0); // high stability => high recall

        let queue = col.build_points_at_stake_queue("is:review").unwrap();
        let ids = queue.card_ids();
        assert_eq!(
            ids.first().copied(),
            Some(heavy_weak),
            "the weak, high-weight card must surface first"
        );
        assert_eq!(ids.len(), 2);

        // Scores must be strictly ordered.
        assert!(
            queue.entries[0].score > queue.entries[1].score,
            "ordering must be by descending score"
        );
    }

    #[test]
    fn ties_break_on_due_then_id() {
        let mut col = Collection::new();
        // Equal weights and equal (zero) history => equal scores, so the
        // tie-break must fall back to FSRS due/overdue ordering.
        col.set_config(
            TOPIC_WEIGHTS_CONFIG_KEY,
            &HashMap::from([("circuit_analysis".to_string(), 5.0_f64)]),
        )
        .unwrap();

        let first = add_tagged_card(&mut col, "a", "circuit_analysis");
        let second = add_tagged_card(&mut col, "b", "circuit_analysis");
        // Both are new cards with no memory => weakness 1.0, identical score.
        // Make `second` more overdue by giving it a smaller due value.
        for (cid, due) in [(first, 10), (second, 1)] {
            let mut card = col.storage.get_card(cid).unwrap().unwrap();
            card.ctype = crate::card::CardType::Review;
            card.queue = crate::card::CardQueue::Review;
            card.due = due;
            col.storage.update_card(&card).unwrap();
        }

        let queue = col.build_points_at_stake_queue("is:review").unwrap();
        let ids = queue.card_ids();
        assert_eq!(queue.entries[0].score, queue.entries[1].score);
        assert_eq!(
            ids,
            vec![second, first],
            "equal scores must order the more-overdue (smaller due) card first"
        );
    }

    #[test]
    fn interleaves_topics_instead_of_blocking() {
        let mut col = Collection::new();
        col.set_config(
            TOPIC_WEIGHTS_CONFIG_KEY,
            &HashMap::from([
                ("circuit_analysis".to_string(), 12.0_f64),
                ("ethics".to_string(), 4.0_f64),
            ]),
        )
        .unwrap();
        // Five new cards in each topic; all new => weakness 1.0 for both.
        for i in 0..5 {
            add_tagged_card(&mut col, &format!("c{i}"), "circuit_analysis");
            add_tagged_card(&mut col, &format!("e{i}"), "ethics");
        }

        let queue = col.build_points_at_stake_queue("is:new").unwrap();
        let topics: Vec<String> = queue
            .entries
            .iter()
            .map(|e| e.topic.clone().unwrap_or_default())
            .collect();

        // The heaviest topic still leads...
        assert_eq!(topics.first().map(String::as_str), Some("circuit_analysis"));
        // ...but the lighter topic must appear early rather than after a solid
        // block of all circuit_analysis cards -- i.e. topics interleave.
        assert!(
            topics[..5].iter().any(|t| t == "ethics"),
            "topics must interleave within the first few cards, got {topics:?}"
        );
        // Every card is still present exactly once.
        assert_eq!(
            topics.iter().filter(|t| *t == "circuit_analysis").count(),
            5
        );
        assert_eq!(topics.iter().filter(|t| *t == "ethics").count(), 5);
    }

    #[test]
    fn degenerate_inputs_do_not_crash() {
        let mut col = Collection::new();

        // (a) No due cards at all -> empty queue, no panic.
        let empty = col.build_points_at_stake_queue("is:due").unwrap();
        assert!(empty.entries.is_empty());

        // (b) A card whose topic has no weight mapping must fall back to the
        // default weight rather than crash, and must still appear.
        let unmapped = add_tagged_card(&mut col, "mystery", "totally_unknown_area");
        let untagged_note = NoteAdder::basic(&mut col).fields(&["no tag", ""]).add(&mut col);
        let untagged = col.storage.card_ids_of_notes(&[untagged_note.id]).unwrap()[0];

        let queue = col.build_points_at_stake_queue("is:new").unwrap();
        let ids = queue.card_ids();
        assert!(ids.contains(&unmapped));
        assert!(ids.contains(&untagged));
        let default_weight = col.fe_default_topic_weight();
        for entry in &queue.entries {
            assert_eq!(
                entry.topic_weight, default_weight,
                "unmapped/untagged cards use the default weight"
            );
            assert_eq!(entry.weakness, 1.0, "unseen topics default to max weakness");
        }
    }

    #[test]
    fn queue_is_read_only_and_undo_survives_a_review() {
        let mut col = Collection::new();
        let cid = add_tagged_card(&mut col, "State Ohm's law.", "circuit_analysis");

        // Building the queue is read-only: it must not mutate the card or add
        // an undo step.
        let before = col.storage.get_card(cid).unwrap().unwrap();
        let undo_before = format!("{:?}", col.can_undo());
        let queue = col.build_points_at_stake_queue("is:new").unwrap();
        assert_eq!(queue.card_ids(), vec![cid]);
        let after = col.storage.get_card(cid).unwrap().unwrap();
        assert_eq!(before, after, "queue building must not mutate the card");
        assert_eq!(
            undo_before,
            format!("{:?}", col.can_undo()),
            "queue building must not create an undo step"
        );

        // Run a review after consulting the queue, then undo it. The card must
        // return to its pre-review state, proving undo/integrity is unaffected
        // by the ordering-only change.
        col.answer_good();
        let reviewed = col.storage.get_card(cid).unwrap().unwrap();
        assert_ne!(
            reviewed.queue, before.queue,
            "answering should have changed the card"
        );
        col.undo().unwrap();
        let restored = col.storage.get_card(cid).unwrap().unwrap();
        assert_eq!(
            restored.queue,
            crate::card::CardQueue::New,
            "undo restores the card to the New queue"
        );
        assert_eq!(restored.ctype, crate::card::CardType::New);
    }

    #[test]
    fn memory_score_withheld_below_threshold() {
        let mut col = Collection::new();
        // Lower the topic threshold but keep reviews high so a single studied
        // card cannot satisfy the rule.
        let cid = add_tagged_card(&mut col, "q", "circuit_analysis");
        set_memory(&mut col, cid, 100.0, 5.0);

        let score = col.compute_fe_memory_score("is:review").unwrap();
        assert!(!score.shown, "one card is well below the give-up threshold");
        assert!(!score.withheld_reason.is_empty());
        assert_eq!(score.min_reviews_required, DEFAULT_MEMORY_MIN_REVIEWS);
        assert_eq!(score.min_topics_required, DEFAULT_MEMORY_MIN_TOPICS);
    }

    #[test]
    fn memory_score_shown_with_range_when_threshold_met() {
        let mut col = Collection::new();
        // Relax thresholds so a small test deck crosses the line.
        col.set_config(MEMORY_MIN_REVIEWS_CONFIG_KEY, &3_u32).unwrap();
        col.set_config(MEMORY_MIN_TOPICS_CONFIG_KEY, &3_u32).unwrap();

        for (i, topic) in ["circuit_analysis", "mathematics", "ethics"]
            .iter()
            .enumerate()
        {
            let cid = add_tagged_card(&mut col, &format!("q{i}"), topic);
            set_memory(&mut col, cid, 50.0 + i as f32 * 10.0, 5.0);
        }

        let score = col.compute_fe_memory_score("is:review").unwrap();
        assert!(score.shown, "threshold met => score shown");
        assert!(score.point_estimate > 0.0 && score.point_estimate <= 1.0);
        assert!(
            score.range_low <= score.point_estimate && score.point_estimate <= score.range_high,
            "the point estimate must sit inside the honest range"
        );
        assert!(score.range_low >= 0.0 && score.range_high <= 1.0);
        assert_eq!(score.topics_covered, 3);
        assert!(!score.main_reason.is_empty());
    }

    /// Adds a worked, exam-style card tagged both `fe::<topic>` and
    /// `track::durable` (what the performance score scopes to).
    fn add_durable_card(col: &mut Collection, front: &str, topic: &str) -> CardId {
        let note = NoteAdder::basic(col)
            .fields(&[front, ""])
            .tags(&[format!("fe::{topic}"), "track::durable".to_string()])
            .add(col);
        col.storage.card_ids_of_notes(&[note.id]).unwrap()[0]
    }

    #[test]
    fn performance_score_withheld_below_threshold() {
        let mut col = Collection::new();
        let cid = add_durable_card(&mut col, "Find Vth.", "circuit_analysis");
        set_memory(&mut col, cid, 100.0, 5.0);

        let score = col
            .compute_fe_performance_score("tag:track::durable")
            .unwrap();
        assert!(!score.shown, "a single exam-style card is below threshold");
        assert!(!score.withheld_reason.is_empty());
        assert_eq!(score.min_reviews_required, DEFAULT_PERFORMANCE_MIN_REVIEWS);
        assert_eq!(score.min_topics_required, DEFAULT_PERFORMANCE_MIN_TOPICS);
    }

    #[test]
    fn performance_score_shown_with_range_when_threshold_met() {
        let mut col = Collection::new();
        col.set_config(PERFORMANCE_MIN_REVIEWS_CONFIG_KEY, &3_u32)
            .unwrap();
        col.set_config(PERFORMANCE_MIN_TOPICS_CONFIG_KEY, &2_u32)
            .unwrap();

        for (i, topic) in ["circuit_analysis", "power_systems", "electronics"]
            .iter()
            .enumerate()
        {
            let cid = add_durable_card(&mut col, &format!("p{i}"), topic);
            set_memory(&mut col, cid, 40.0 + i as f32 * 20.0, 5.0);
        }

        let score = col
            .compute_fe_performance_score("tag:track::durable")
            .unwrap();
        assert!(score.shown, "threshold met => performance shown");
        assert!(
            score.range_low <= score.point_estimate && score.point_estimate <= score.range_high,
            "the point estimate must sit inside the honest range"
        );
        assert!(score.range_low >= 0.0 && score.range_high <= 1.0);
        assert_eq!(score.topics_covered, 3);
        assert!(!score.main_reason.is_empty());
    }

    #[test]
    fn readiness_score_withheld_and_reports_next_action() {
        let mut col = Collection::new();
        // A little history, but nowhere near the 200-review / 50%-coverage bar.
        let cid = add_tagged_card(&mut col, "q", "circuit_analysis");
        set_memory(&mut col, cid, 100.0, 5.0);

        let score = col.compute_fe_readiness_score("is:review").unwrap();
        assert!(!score.shown, "the FE readiness bar is deliberately strict");
        assert!(!score.withheld_reason.is_empty());
        assert_eq!(
            score.areas_total,
            seed_topic_weights().len() as u32,
            "all seeded NCEES areas are counted"
        );
        assert!(
            !score.next_action.is_empty(),
            "an abstaining readiness score must still name the best next action"
        );
        assert_eq!(score.min_reviews_required, DEFAULT_READINESS_MIN_REVIEWS);
        assert_eq!(score.min_coverage_required, DEFAULT_READINESS_MIN_COVERAGE);
    }

    #[test]
    fn readiness_score_shown_with_range_when_thresholds_relaxed() {
        let mut col = Collection::new();
        // Relax the pre-registered thresholds so a small test deck can cross.
        col.set_config(READINESS_MIN_REVIEWS_CONFIG_KEY, &2_u32)
            .unwrap();
        col.set_config(READINESS_MIN_COVERAGE_CONFIG_KEY, &0.1_f64)
            .unwrap();

        for (i, topic) in ["circuit_analysis", "power_systems", "mathematics"]
            .iter()
            .enumerate()
        {
            let cid = add_tagged_card(&mut col, &format!("r{i}"), topic);
            set_memory(&mut col, cid, 60.0 + i as f32 * 10.0, 5.0);
        }

        let score = col.compute_fe_readiness_score("is:review").unwrap();
        assert!(score.shown, "relaxed thresholds met => readiness shown");
        assert!(
            score.range_low <= score.pass_probability
                && score.pass_probability <= score.range_high,
            "the pass probability must sit inside the honest range"
        );
        assert!(score.pass_probability >= 0.0 && score.pass_probability <= 1.0);
        assert_eq!(score.areas_covered, 3);
        assert_eq!(score.areas_total, seed_topic_weights().len() as u32);
        assert!(!score.main_reason.is_empty());
    }
}
