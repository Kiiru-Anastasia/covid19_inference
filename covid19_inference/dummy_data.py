import logging
import numpy as np
from numpy import exp
import datetime
from scipy.stats import halfcauchy, rv_discrete, nbinom
from scipy.special import binom, gammaln as gamln
import pandas as pd

log = logging.getLogger(__name__)


class DummyData(object):
    """
    Class for generating a random dataset, can be used for testing the model.

    Example
    -------

        .. code-block::

            #Create dates for data generation
            data_begin = datetime.datetime(2020,3,10)
            data_end = datetime.datetime(2020,4,26)

            # Create dummy data object
            dd = cov19.dummy_data.DummyData(data_begin,data_end)        

            #We can look at our initially generated values and tweak them by the attribute `dd.initials`.
            #If we are happy with the initial values we can generate our dummy data set.
            dd.generate()
            The generated data is accessible by the attribute `dd.data` as pandas dataframe.
    """

    def __init__(
        self,
        data_begin,
        data_end,
        mu=0.13,
        noise=False,
        auto_generate=False,
        seed=None,
        **initial_values,
    ):
        """
        Creates a dummy dataset from initial values, these initial values get randomly
        generated or can be given via a dict.

        Parameters
        ----------
        data_begin : datetime.datetime
            Start date for the dataset
        data_end : datetime.datetime
            End date for the dataset
        mu : number
            Value for the recovery rate
        noise : bool
            Add random noise to the output
        auto_generate : bool
            Whether or not to generate a dataset on class init. Calls the :py:meth:`generate` method.
        seed : number
            Seed for the random number generation. Also accessible by `self.seed` later on. 
        initial_values : dict
        """
        if seed is not None:
            self.seed = seed
        else:
            self.seed = np.randint(1000000,9999999)
        np.random.seed(self.seed)

        self.add_noise = noise
        self.data_begin = data_begin
        self.data_end = data_end
        self.mu = mu

        self._update_initial(**initial_values)

        if auto_generate:
            self.generate()

    @property
    def dates(self):
        return pd.date_range(self.data_begin, self.data_end)

    @property
    def data_len(self):
        return (self.data_end - self.data_begin).days

    @property
    def get_lambda_t(self):
        """Return lambda_t as dataframe with datetime index"""
        df = pd.DataFrame()
        df["date"] = self.dates
        df["lambda_t"] = self.lambda_t
        df = df.set_index("date")
        return df

    def _update_initial(self, **initial_values):
        """
        Generates the initial values for a lot of attributes that get used by the following functions.
        Some are generated random uniform some are generated from a normal distribution and even other
        ones are just set to a fixed value.
        
        They can be set/changed by editing the attributes before running :py:meth:`generate` or by passing
        a keyword dict into the class at initialization.
        """

        def _generate_change_points_we():
            """
            Generates random change points each weekend in the give time period.

            The date is normal distributed with mean on each Saturday and a variance of 3 days.

            The lambda is draw uniform between 0 and 1
            """
            change_points = []
            for date in pd.date_range(self.data_begin, self.data_end):
                if date.weekday() == 6:
                    # Draw a date
                    date_index = int(np.random.normal(0, 3))
                    date_random = date + datetime.timedelta(days=date_index)
                    # Draw a lambda
                    lambda_t = np.random.uniform(0, 1)
                    change_points.append([date_random, lambda_t])
            return change_points



        self.initials = {}

        if "S_initial" in initial_values:
            self.initials["S_initial"] = initial_values.get("S_initial")
        else:
            self.initials["S_initial"] = np.random.randint(5000000, 10000000)

        if "I_initial" in initial_values:
            self.initials["I_initial"] = initial_values.get("I_initial")
        else:
            self.initials["I_initial"] = int(halfcauchy.rvs(loc=3))

        if "R_initial" in initial_values:
            self.initials["R_initial"] = initial_values.get("R_initial")
        else:
            self.initials["R_initial"] = 0

        if "lambda_initial" in initial_values:
            self.initials["lambda_initial"] = initial_values.get("lambda_initial")
        else:
            self.initials["lambda_initial"] = np.random.uniform(0, 1)

        if "change_points" in initial_values:
            self.initials["change_points"] = initial_values.get("change_points")
        else:
            self.initials["change_points"] = _generate_change_points_we()

        if "noise_factor" in initial_values:
            self.initials["noise_factor"] = initial_values.get("noise_factor")
        else:
            self.initials["noise_factor"] = 0.00001

        if "offset_sunday" in initial_values:
            self.initials["offset_sunday"] = initial_values.get("offset_sunday")
        else:
            d = self.data_begin
            offset = 0
            while d.weekday() != 6: # 0=Monday 6=Sunday
                d += datetime.timedelta(days=1)
                offset += 1
            self.initials["offset_sunday"] = offset

        if "weekend_factor" in initial_values:
            self.initials["weekend_factor"] = initial_values.get("weekend_factor")
        else:
            self.initials["weekend_factor"] = np.random.uniform(0.1, 0.5)


        if "case_delay" in initial_values:
            self.initials["case_delay"] = initial_values.get("case_delay")
        else:
            self.initials["case_delay"] = 6

        return self.initials

    def generate(self):
        r"""
        Generates a dummy dataset with the given initial values by performing the following steps.

        #. Converts given change points to daily lambda_t values :py:meth:`_change_points_to_lambda_t`.
        #. Numerically solving SIR model with the given initial values e.g. lambda_t :py:meth:`_generate_SIR`.
        #. Calculate new cases raw from SIR data :py:meth:`_calc_new_cases_raw`. 
        #. Add week modulation onto new cases raw :py:meth:`_week_modulation`. 
        #. Delay new cases :py:meth:`_delay_cases`.
        #. Add noise onto the dataset :py:meth:`_random_noise`.

        Returns
        -------
        dataset : pandas.dataFrame
        """

        # ------------------------------------------------------------------------------ #
        # 1. Create daily lambda_t values from change points
        # ------------------------------------------------------------------------------ #
        self.lambda_t = self._change_points_to_lambda_t()

        # ------------------------------------------------------------------------------ #
        # 2. solve SIR differential equation by RK4
        # ------------------------------------------------------------------------------ #
        t_n, data = self._generate_SIR() #data[:,0]=S data[:,1]=I data[:,2]=R

        # ------------------------------------------------------------------------------ #
        # 3. calculate new cases raw
        # ------------------------------------------------------------------------------ #
        new_cases_raw = self._calc_new_cases_raw(data[:, 1])

        # ------------------------------------------------------------------------------ #
        # 4. Week modulation
        # ------------------------------------------------------------------------------ #
        new_cases = self._week_modulation(t_n, new_cases_raw)

        # ------------------------------------------------------------------------------ #
        # 5. Construct dataframe and delay cases
        # ------------------------------------------------------------------------------ #
        df = pd.DataFrame()
        df["date"] = pd.date_range(self.data_begin, self.data_end)
        df["new_cases"] = new_cases
        df = df.set_index("date")
        df = self._delay_cases(df)
        df["lambda_t"] = self.lambda_t

        # ------------------------------------------------------------------------------ #
        # 6. Add noise onto the data
        # ------------------------------------------------------------------------------ #
        if self.add_noise:
            df["new_cases"] = self._random_noise(df["new_cases"])


        return df

    def _change_points_to_lambda_t(self):
        """
        Generates a lambda_t array from the change points `self.initials["change_points"]`.
        
        TODO
        ----
        Documentation 

        """

        # We create an lambda_t array for each date in our time period

        def helper_lambda_between(cp1,cp2):
            """
            Helper function to get lambda values between two change points
            """
            #Normalize cp1 to start at x=0
            delta_days = np.abs((cp1[0]-cp2[0]).days)

            #For the timerange between cp1 and cp2 construct each lambda value 
            lambda_t_temp = []
            for x in range(delta_days):
                lambda_t_temp.append(logistics_from_cps(x,0,cp1[1],delta_days,cp2[1]))
            return lambda_t_temp

        def logistics_from_cps(x, cp1_x, cp1_y, cp2_x, cp2_y):
            """
                Calculates the lambda value at the point x
                between cp1_x and cp2_x.

                TODO
                ----
                implement k value 
            """
            L = cp2_y-cp1_y
            C = cp1_y
            x_0 = np.abs(cp1_x - cp2_x)/2 + cp1_x
            #log.debug(f"{L} {C} {x_0} {x}")
            #print(L/(1+np.exp(-4*(x-x_0)))+C)
            return L/(1+np.exp(-0.8*(x-x_0)))+C

        # ------------------------------------------------------------------------------ #
        # Start
        # ------------------------------------------------------------------------------ #
        change_points = self.initials["change_points"]
        change_points.sort(key=lambda x: x[0])
        change_points = np.array(change_points)

        lambda_t = [ helper_lambda_between([self.data_begin,self.initials["lambda_initial"]],change_points[0])]
        for i, value in enumerate(change_points):
            if (i == len(change_points)-1):
                continue
            lambda_t = np.append(lambda_t, helper_lambda_between(change_points[i],change_points[i+1]))

        lambda_t = np.append(lambda_t,[change_points[-1][1]] * ((self.data_end - change_points[-1][0]).days + 1))

        return lambda_t.flatten()



    def _generate_SIR(self):
        r"""        
        Numerically solves the differential equation

        .. math::
            \frac{dy}{dt}  = \lambda(t) \cdot
            \begin{bmatrix}
                -1 \\ 1 \\ 0 
            \end{bmatrix}
            \frac{y_0 \cdot y_1}{N} -
            \begin{bmatrix}
                0 \\ \mu y_1 \\ -\mu y_1
            \end{bmatrix}

        using runge kutta 4, whereby

        .. math::
            y = \begin{bmatrix}
                S \\ I \\ R 
            \end{bmatrix}
            = \begin{bmatrix}
                y_0 \\ y_1 \\ y_2 
            \end{bmatrix}

        and the population size :math:`N` is the sum of :math:`S`, :math:`I` and :math:`R`

        The initial SIR parameters and the force of infection :math:`\lambda(t)` are obtained from the object.
        
        Attributes
        ----------
        initials["S_initial"] : :math:`S_0`
            Number of initial susceptible
        initials["I_initial"] : :math:`I_0`
            Number of initial infected 
        initials["R_initial"] : :math:`I_0`
            Number of initial recovered
        lambda_t : :math:`\lambda(t)`
            lambda_t array for each time step. Generated by :py:meth:`_change_points_to_lambda_t`

        Return
        ------
        t_n, y_n : array array ,1-dim
            Returns the time steps t_n and the time series of the S I R values y_n as vector
        """

        # SIR model as vector
        def f(t, y, lambda_t):
            return lambda_t * np.array([-1.0, 1.0, 0.0]) * y[0] * y[1] / N + np.array(
                [0, -self.mu * y[1], self.mu * y[1]]
            )

        # Runge_Kutta_4 timesteps
        def RK4(dt, t_n, y_n, lambda_t):
            k_1 = f(t_n, y_n, lambda_t)
            k_2 = f(t_n + dt / 2, y_n + k_1 * dt / 2, lambda_t)
            k_3 = f(t_n + dt / 2, y_n + k_2 * dt / 2, lambda_t)
            k_4 = f(t_n + dt, y_n + k_3 * dt, lambda_t)
            return y_n + dt / 6 * (k_1 + 2 * k_2 + 2 * k_3 + k_4)

        # ------------------------------------------------------------------------------ #
        # Preliminar parameters
        # ------------------------------------------------------------------------------ #
        time_range = self.data_len
        N = (
            self.initials["S_initial"]
            + self.initials["I_initial"]
            + self.initials["R_initial"]
        )
        t_n = [0]
        y_n = [
            np.array(
                [
                    self.initials["S_initial"],
                    self.initials["I_initial"],
                    self.initials["R_initial"],
                ]
            )
        ]
        dt = 1

        # ------------------------------------------------------------------------------ #
        # Timesteps
        # ------------------------------------------------------------------------------ #
        for i in range(time_range):
            # Check if lambda_t has to change (initial value is also set by this)
            t_n.append(t_n[i] + dt)
            y_n.append(RK4(dt, t_n[i], y_n[i], self.lambda_t[i]))

        return t_n, np.array(y_n)

    def _calc_new_cases_raw(self, I):
        r"""
        Since the solved SIR model also gives us declining cases, which we can't observe from
        real data, we have to manipulate our data to look more like real observed data.
        
        .. math:: 
            \text{new\_cases\_raw}_i = \begin{cases} I_i - I_{i-1} & (I_i - I_{i-1})>0\\
            0 & \text{otherwise}
            \end{cases}

        Parameters
        ----------
        I : array, 1-dim
            Time series of the infected people
        """
        y = []
        for i in range(len(I)):
            if i == 0:
                y.append(0) #to get the same arrays size for new cases and cumulative cases
                continue
            value = I[i]-I[i-1]
            if value > 0:
                y.append(value)
            else:
                y.append(0)

        return y

    def _week_modulation(self, t_n, new_cases_raw):
        r"""
        Adds week modulation on top of the number of new cases

        .. math:: 
            \text{new\_cases} = \text{new\_cases\_raw} \cdot (1-f(t)),
    
        with

        .. math::
            f(t) = f_w \cdot (1-|\sin(\frac{\pi}{7}t-\frac{1}{2}\Phi_w)|)


        Parameters
        ----------
        t_n : array 1-dim
            time steps
        new_cases_raw : array 1-dim
            
        Attributes
        ----------
        initials["weekend_factor"] : :math:`f_w`
            Gets randomly generated on initialization of the class or can be passed via kwarg see :py:class:`DummyData`.
        initials["offset_sunday"] : :math:`\Phi_w`
            Gets generated on initialization of the class or can be passed manually via kwarg see :py:class:`DummyData`.
        """        
        def f(t):
            sin = np.sin(np.pi/7*t-1/2*self.initials["offset_sunday"])
            return self.initials["weekend_factor"] * (1-np.abs(sin))
        new_cases = []
        for i in range(len(t_n)):
            new_cases.append(new_cases_raw[i]*(1-f(t_n[i])))

        return new_cases

    def _delay_cases(self, new_cases):
        """
        Shifts each column of a given pandas dataframe by 10 days. This is done to simulate delayed cases.
        
        Parameters
        ----------
        df : pandas.dataFrame


        Attributes
        ----------
        initials["case_delay"] : 

        Returns
        -------
        df : pandas.dataFrame
            delayed cases

        TODO
        ----
        Look into this a bit more and implement it the right way 
        """
        new_cases = new_cases.shift(periods=self.initials["case_delay"], fill_value=0)
        return new_cases

    def _random_noise(self, df):
        r"""
        Generates random noise on an observable by a Negative Binomial :math:`NB`.
        References to the negative binomial can be found `here <https://ncss-wpengine.netdna-ssl.com/wp-content/themes/ncss/pdf/Procedures/NCSS/Negative_Binomial_Regression.pdf>`_
        .

        .. math::
            O &\sim NB(\mu=datapoint,\alpha)
        
        We keep the alpha parameter low to obtain a small variance which should than always be approximately the size of the mean.

        Parameters
        ----------
        df : new_cases , pandas.DataFrame
            Observable on which we want to add the noise

        Attributes
        ----------
        initials["noise_factor"] : :math:`\alpha`
            Alpha factor for the random number generation

        Returns
        -------
        array : 1-dim
            observable with added noise
        """

        def convert(mu, alpha):
            r = 1 / alpha
            p = mu / (mu + r)
            return r, 1 - p

        nbinom.random_state = np.random.RandomState(self.seed)
        array = df.values

        for i in range(len(array)):
            if array[i] == 0:
                continue
            log.debug(f"Data {array[i]}")
            r, p = convert(array[i], self.initials["noise_factor"])
            log.debug(f"n {r}, p {p}")
            mean, var = nbinom.stats(r, p, moments="mv")
            log.debug(f"mean {mean} var {var}")
            array[i] = nbinom.rvs(r, p)
            log.debug(f"Drawn {array[i]}")

        return array

    def _create_cumulative(self, array):
        r"""
        Since we cant measure a negative number of new cases. All the negative values are cut from the I/R array
        and the counts are added up to create the cumulative/total cases dataset.
        """
        # Confirmed
        diff = [array[0]]
        for i in range(1, len(array)):
            if (array[i] - array[i - 1]) > 0:
                diff.append(array[i] - array[i - 1] + diff[i - 1])
            else:
                diff.append(diff[i - 1])

        return diff