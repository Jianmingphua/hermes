"""
Forex Trading Bot - Kalman Filter Signal Module
=================================================
Univariate Kalman filter for price trend estimation.

State vector: [price, velocity]
  - price: estimated "true" underlying price (de-noised)
  - velocity: rate of change (directional momentum)

Measurement: observed close price (noisy)

The Kalman filter recursively:
  1. Predicts next state from current state
  2. Compares prediction to actual measurement
  3. Updates state estimate using Kalman gain (weighted average)

Outputs:
  - kalman_price: filtered price estimate
  - kalman_velocity: estimated trend direction/speed
  - kalman_confidence: inverse of estimation uncertainty (0-1)
  - kalman_signal: BUY/SELL/HOLD based on velocity + confidence

Usage:
  from src.kalman_filter import KalmanFilterEstimator
  kf = KalmanFilterEstimator()
  result = kf.analyze(df['close'])
"""

import logging
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

try:
    from pykalman import KalmanFilter as PyKalmanFilter
    HAS_PYKALMAN = True
except ImportError:
    HAS_PYKALMAN = False
    logger.warning("pykalman not available — Kalman filter disabled")


class KalmanFilterEstimator:
    """
    Kalman filter for forex price trend estimation.

    State space model:
        State:    x = [price, velocity]^T
        Measure:  z = price (observed close)

        Transition:  x_t = F @ x_{t-1} + w_t    (w_t ~ N(0, Q))
        Measurement: z_t = H @ x_t + v_t          (v_t ~ N(0, R))

    Where:
        F = [[1, dt], [0, 1]]  (constant velocity model, dt=1)
        H = [1, 0]              (we only observe price)
        Q = process noise (how much we expect the trend to change)
        R = measurement noise (how noisy is the price)
    """

    def __init__(
        self,
        process_noise: float = 0.01,
        measurement_noise: float = 1.0,
        velocity_threshold: float = 0.00005,
        confidence_threshold: float = 0.3,
    ):
        """
        Args:
            process_noise: Q diagonal scale — higher = more adaptive but noisier.
                           For M15 forex, 0.01 is a good starting point.
            measurement_noise: R value — higher = trust measurements less.
                               For M15 forex, 1.0 works well.
            velocity_threshold: Minimum |velocity| to consider a trend (not flat).
                                For major pairs on M15, ~0.00005 (0.5 pips/period).
            confidence_threshold: Minimum Kalman confidence to emit a signal.
        """
        self.process_noise = process_noise
        self.measurement_noise = measurement_noise
        self.velocity_threshold = velocity_threshold
        self.confidence_threshold = confidence_threshold

    def _build_filter(self, n_timesteps: int) -> "PyKalmanFilter":
        """Build a PyKalmanFilter with our state space model."""
        # Transition matrix: constant velocity model
        F = np.array([[1.0, 1.0],
                       [0.0, 1.0]])

        # Observation matrix: we only observe price
        H = np.array([[1.0, 0.0]])

        # Process noise covariance
        # Higher values = expect more change in trend (more adaptive)
        Q = np.eye(2) * self.process_noise

        # Measurement noise covariance
        # Higher values = trust measurements less (more smoothing)
        R = np.array([[self.measurement_noise]])

        # Initial state: first price, zero velocity
        # Will be set via em() or manually

        kf = PyKalmanFilter(
            transition_matrices=F,
            observation_matrices=H,
            transition_covariance=Q,
            observation_covariance=R,
            n_dim_state=2,
            n_dim_obs=1,
        )
        return kf

    def analyze(self, close_series: pd.Series) -> dict:
        """
        Run Kalman filter on a price series.

        Args:
            close_series: pandas Series of close prices (min ~50 bars recommended)

        Returns:
            dict with:
              - kalman_price: filtered price (last value)
              - kalman_velocity: estimated velocity (last value)
              - kalman_confidence: filter confidence 0-1 (last value)
              - kalman_signal: BUY / SELL / HOLD
              - kalman_score: signed score for signal aggregation
              - kalman_fired: bool, whether this counts as a confirmation
              - velocity_series: full velocity array (for backtesting)
              - confidence_series: full confidence array
        """
        if not HAS_PYKALMAN:
            return self._null_result()

        if close_series is None or len(close_series) < 50:
            return self._null_result()

        prices = close_series.values.astype(float)
        n = len(prices)

        try:
            kf = self._build_filter(n)

            # Use EM to estimate good initial parameters from the data
            # This adapts Q and R to the specific pair's volatility
            # Limit EM iterations for speed on long series
            em_iter = min(5, max(2, n // 500))
            kf = kf.em(prices, n_iter=em_iter)

            # Run the filter
            state_means, state_covs = kf.filter(prices.reshape(-1, 1))

            # Extract results
            kalman_prices = state_means[:, 0]
            kalman_velocities = state_means[:, 1]

            # Confidence: inverse of the price variance (normalized)
            # state_covs[i, 0, 0] = variance of price estimate at time i
            price_variances = state_covs[:, 0, 0]
            # Normalize: lower variance = higher confidence
            max_var = np.nanmax(price_variances)
            if max_var > 0:
                kalman_confidences = 1.0 - (price_variances / max_var)
            else:
                kalman_confidences = np.ones(n) * 0.5

            # Current values
            current_velocity = kalman_velocities[-1]
            current_confidence = kalman_confidences[-1]
            current_kalman_price = kalman_prices[-1]
            current_price = prices[-1]

            # Determine signal
            abs_velocity = abs(current_velocity)

            if abs_velocity > self.velocity_threshold and current_confidence >= self.confidence_threshold:
                if current_velocity > 0:
                    signal = "BUY"
                    # Score: velocity magnitude × confidence, scaled to match
                    # existing indicator weights (EMA ±3, MACD ±2.5, etc.)
                    raw_score = min(abs_velocity / self.velocity_threshold * current_confidence * 2.5, 2.5)
                else:
                    signal = "SELL"
                    raw_score = -min(abs_velocity / self.velocity_threshold * current_confidence * 2.5, 2.5)
                fired = True
            else:
                signal = "HOLD"
                raw_score = 0.0
                fired = False

            return {
                "kalman_price": round(float(current_kalman_price), 6),
                "kalman_velocity": round(float(current_velocity), 8),
                "kalman_confidence": round(float(current_confidence), 4),
                "kalman_signal": signal,
                "kalman_score": round(raw_score, 3),
                "kalman_fired": fired,
                "velocity_series": kalman_velocities,
                "confidence_series": kalman_confidences,
                "kalman_prices": kalman_prices,
            }

        except Exception as e:
            logger.warning("Kalman filter failed: %s — returning null result", e)
            return self._null_result()

    def _null_result(self) -> dict:
        """Return a null result when filter is unavailable or fails."""
        return {
            "kalman_price": None,
            "kalman_velocity": None,
            "kalman_confidence": 0.0,
            "kalman_signal": "HOLD",
            "kalman_score": 0.0,
            "kalman_fired": False,
            "velocity_series": None,
            "confidence_series": None,
            "kalman_prices": None,
        }

    @staticmethod
    def add_columns(df: pd.DataFrame) -> pd.DataFrame:
        """
        Add Kalman filter columns to a DataFrame (for backtesting).
        Adds: kalman_velocity, kalman_confidence, kalman_signal

        This is a batch operation that runs the filter once and adds
        the full series as columns.
        """
        if not HAS_PYKALMAN or df.empty or len(df) < 50:
            df["kalman_velocity"] = 0.0
            df["kalman_confidence"] = 0.0
            df["kalman_signal"] = "HOLD"
            df["kalman_score"] = 0.0
            df["kalman_fired"] = False
            return df

        estimator = KalmanFilterEstimator()
        result = estimator.analyze(df["close"])

        if result["velocity_series"] is not None:
            df["kalman_velocity"] = result["velocity_series"]
            df["kalman_confidence"] = result["confidence_series"]
        else:
            df["kalman_velocity"] = 0.0
            df["kalman_confidence"] = 0.0

        # Per-row signal based on velocity and confidence
        vel = df["kalman_velocity"]
        conf = df["kalman_confidence"]
        threshold = estimator.velocity_threshold
        conf_threshold = estimator.confidence_threshold

        conditions = [
            (vel > threshold) & (conf >= conf_threshold),
            (vel < -threshold) & (conf >= conf_threshold),
        ]
        choices = ["BUY", "SELL"]
        df["kalman_signal"] = np.select(conditions, choices, default="HOLD")

        # Per-row score
        abs_vel = vel.abs()
        score = abs_vel / threshold * conf * 2.5
        score = score.clip(upper=2.5)
        df["kalman_score"] = np.where(
            vel > threshold,
            score,
            np.where(vel < -threshold, -score, 0.0),
        )

        # Fired: velocity and confidence both above thresholds
        df["kalman_fired"] = (abs_vel > threshold) & (conf >= conf_threshold)

        return df
