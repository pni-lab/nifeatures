from .transform import DisplacementInvariantTransformer
from sklearn.model_selection import KFold
from sklearn.pipeline import Pipeline
from joblib import Parallel, delayed
import pandas as pd
import numpy as np
import itertools
import joblib


def _update_parameters(parameters, settings):
    param_keys = list(parameters.keys())
    settings_keys = list(settings.keys())

    duplicates = [duplicate for duplicate in settings_keys
                  if duplicate in param_keys]

    if len(duplicates) > 0:
        for element in duplicates:
            del settings[element]

    parameters.update(settings)
    return parameters


def _get_key_parameters(p_grid, keys, values, ID):

    values = np.array(values, dtype="object")

    selected = [True if key.startswith(ID) else False for key in p_grid.keys()]

    selected_keys = list(itertools.compress(keys, selected))
    selected_values = list(itertools.compress(values, selected))
    key_params = dict(zip(selected_keys, selected_values))

    return selected_keys, selected_values, key_params


def _get_search_params(X, y, p_grid, ID, iteration=None):
    Xy_hash = {"X": joblib.hash(str(X)), "y": joblib.hash(str(y))}

    # Get GridSearch parameters for the current iteration;
    keys = [key.split("__")[1] for key in p_grid.keys()]
    values = list(itertools.product(*p_grid.values()))[iteration]

    # Get key parameters for the transformer from parameter grid;
    keys, values, params = _get_key_parameters(p_grid, keys, values, ID)
    params = dict(zip(keys, values))

    # Hash X, y and other parameters together;
    hash_params = joblib.hash((str(Xy_hash), str(params)))

    return keys, values, params, hash_params


class TransformerCV:
    def __init__(
            self,
            estimator,
            p_grid,
            *,
            search=None,
            settings=None,
            scoring=None,
            cv=None,
            n_jobs=None
    ):
        self.estimator = estimator
        self.parameters = p_grid
        self.search = search
        self.scoring = scoring

        # If settings is None, the DIT runs with default parameters;
        if settings is None:
            self.settings = dict()
        else:
            self.settings = settings

        if isinstance(cv, int) or (cv is None):
            self.cv = KFold(n_splits=cv)
        else:
            self.cv = cv

        if n_jobs is None:
            self.n_jobs = 1
        else:
            self.n_jobs = n_jobs

        # Define a cache and result variable;
        self._cache = []
        self.precomp = []

    def check_pipeline(self):

        # Check if the transformer is present in the pipeline;
        if not isinstance(self.estimator, Pipeline):
            raise Exception(
                ("The 'estimator' parameter must be a scikit-learn " +
                 "pipeline containing, at least, an instance of " +
                 "Displacement Invariant_Transformer() " +
                 "and a scikit-learn estimator.")
                )

        pipeline_steps = self.estimator.steps
        n_steps = len(self.estimator.steps)
        check_transformer_presence = [
            isinstance(pipeline_steps[i][1],
                       DisplacementInvariantTransformer)
            for i in range(n_steps)
        ]

        # Check whether there is only one instance of DIT() in the pipeline;
        if sum(check_transformer_presence) > 1:
            raise Exception(
                ("Only one instance of Displacement_Invariant_Transformer " +
                 "is allowed in a pipeline.")
            )

        return check_transformer_presence

    def _precompute(self, X, y, p_grid, iteration=None, train_index=None):

        transformer_ID = self.estimator.steps[
            self.check_transformer_presence is True][0]
        X_train, y_train = X[train_index], y[train_index]

        keys, values, params, hash_params = _get_search_params(
            X_train,
            y_train,
            p_grid,
            transformer_ID,
            iteration
        )

        if hash_params not in self._cache:
            # Add transformer settings to the current set of parameters,
            # then precompute data;
            self._cache.append(hash_params)
            params = _update_parameters(params, self.settings)
            coords = DisplacementInvariantTransformer(**params).precompute(
                X_train,
                y_train
            )
        else:
            coords = np.nan

        return (keys, values, hash_params, coords)

    def fit(self, X, y, groups=None):

        if not isinstance(y, np.ndarray):
            y = np.array(y)

        # Check if transformer is present inside Pipeline;
        self.check_transformer_presence = self.check_pipeline()
        is_transformer_present = True in self.check_transformer_presence

        if not is_transformer_present:
            raise Exception(("An instance of " +
                             "Displacement_Invariant_Transformer() " +
                             "must be present in the pipeline"))

        # n_iteration is equal to number of parameter combinations;
        n_iterations = len(list(itertools.product(*self.parameters.values())))

        # Run precompute for every combination of parameters and cv-fold;
        self.precomp.append(Parallel(n_jobs=self.n_jobs)(delayed(
            self._precompute)(X, y, self.parameters, iteration, train_index)
            for train_index, _ in self.cv.split(X, y, groups=groups)
            for iteration in np.arange(n_iterations)))

        # Remove possible NA and return precomputed data as a numpy array;
        self.precomp = pd.DataFrame.from_records(
            self.precomp[0]).dropna().to_numpy()

        if self.search is None:
            return self.precomp

        else:
            # Use settings to set up the trasformer before gridsearch;
            if len(self.settings) > 0:
                for key, value in zip(
                    self.settings.keys(),
                    self.settings.values()
                ):
                    setattr(
                        self.estimator.steps[
                            self.check_transformer_presence is True][1],
                        key,
                        value
                    )

            # If the transformer is present,
            # add precomputed data to its precomp attribute;
            setattr(self.estimator.steps[
                self.check_transformer_presence is True][1],
                "precomp",
                self.precomp
                )

            # Run GridSearch algorithm;
            search = self.search(self.estimator,
                                 self.parameters,
                                 scoring=self.scoring,
                                 cv=self.cv,
                                 n_jobs=self.n_jobs).fit(X, y, groups=groups)

            return search
