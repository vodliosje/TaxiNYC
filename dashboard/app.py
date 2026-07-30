import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
from datetime import datetime

@st.cache_data
def load_data():
    db_clean = pd.read_parquet('../TAXINYC/data/processed/clean_yellow_2025-01.parquet')
    db_rejected = pd.read_parquet('../TAXINYC/data/processed/rejected_yellow_2025-01.parquet')
    
    mart_anomal = pd.read_parquet('../TAXINYC/data/marts/mart_abnormals.parquet').reset_index()
    mart_daily = pd.read_parquet('../TAXINYC/data/marts/mart_daily_demand.parquet').reset_index()
    mart_hourly = pd.read_parquet('../TAXINYC/data/marts/mart_hourly_demand.parquet').reset_index()
    mart_dropoff = pd.read_parquet('../TAXINYC/data/marts/mart_dropoff_common.parquet').reset_index()
    mart_pickup = pd.read_parquet('../TAXINYC/data/marts/mart_pickup_demand.parquet').reset_index()
    mart_tip = pd.read_parquet('../TAXINYC/data/marts/mart_tip_behavior.parquet').reset_index()
    mart_trip_dist = pd.read_parquet('../TAXINYC/data/marts/mart_trip_distribution.parquet').reset_index()
    
    return db_clean, db_rejected, mart_trip_dist, mart_tip,  mart_pickup,mart_dropoff, mart_hourly, mart_daily , mart_anomal

db_clean, db_rejected , mart_trip_dist, mart_tip,  mart_pickup,mart_dropoff, mart_hourly, mart_daily , mart_anomal = load_data()

st.set_page_config(
    page_title='NYC TLC Taxi Yellow Analysis in Jan 2025',
    layout='wide'
)

page = st.sidebar.radio(
    'Select Page',
    [
        'Executive Overview',
        'Demand Patterns',
        'Revenue Patterns',
        'Zone Analysis',
        'Tip Analysis',
        'Data Quality',
        'Anomaly Review'
    ]
)

if page == 'Executive Overview':
    st.title('Executive Overview')
    
    total_revenue = db_clean['total_amount'].sum()
    total_trip = db_clean['tpep_pickup_datetime'].count()
    total_distance = db_clean['trip_distance'].sum()
    
    col1, col2, col3 = st.columns(3)
    
    col1.metric('Total Revenue', f'${total_revenue:.2f}')
    col2.metric('Trip Count', f'${total_trip:.0f}')
    col3.metric('Total Distance', f'${total_distance:.1f}')
    
    st.metric ('Date Range', f'{db_clean['tpep_pickup_datetime'].min()} - {db_clean['tpep_pickup_datetime'].max()} ({db_clean['tpep_pickup_datetime'].max() - db_clean['tpep_pickup_datetime'].min()}')
    
elif page == 'Demand Patterns' :
    st.title('Demand Patterns')
    
    st.header('Hourly Demand')
    st.subheader('')
    
    hourfig, hourax =  plt.subplots(figsize = (10,5))
    hourax.bar(mart_hourly['pickup_hour'],mart_hourly['trip_count'])

    hourax.grid(True, alpha = 0.5, axis = 'y')
    hourax.set_xlabel('Hour')
    hourax.set_ylabel('Trip Count')
    hourax.set_xticks(mart_hourly['pickup_hour'])
    
    st.pyplot(hourfig, width='stretch')
    
    #-------
    st.header('Daily Demand')
    st.subheader('')
    
    daily_fig, daily_ax = plt.subplots(figsize = (10,5))
    daily_ax.bar(mart_daily['pickup_dayofw'],mart_daily['trip_count'])

    daily_ax.grid(True, alpha = 0.5, axis = 'y')
    daily_ax.set_xlabel('Day of Week')
    daily_ax.set_ylabel('Trip Count')
    daily_ax.set_xticks(mart_daily['pickup_dayofw'])
    st.pyplot(daily_fig, width='stretch')
    
elif page == 'Revenue Patterns' :
    st.title('Revenue Patterns')
    
    revenue_day = (
            db_clean.groupby('pickup_date')
            .agg(
                total_passenger = ('passenger_count','sum'),
                trip_count = ('tpep_pickup_datetime','count'),
                avg_trip_distance =('trip_distance','mean'),
                revenue= ("total_amount","sum"),
                avg_tip_rate = ('tip_rate','mean'),
                avg_duration = ('duration_mins','mean')
            )
            .sort_values('pickup_date', ascending = False)
            .reset_index()
        )
    revenue_day['avg_tip_rate'] = revenue_day['avg_tip_rate'] * 100
        
    st.header('Trip count during the month')
    st.subheader('')
    
    month_fig, month_ax = plt.subplots(figsize = (10,5))
    month_ax.bar(revenue_day['pickup_date'],revenue_day['revenue'])

    month_ax.grid(True, alpha = 0.5, axis = 'y')
    month_ax.set_xlabel('Day of Month')
    month_ax.set_ylabel('Revenue')
    month_ax.set_xticks(revenue_day['pickup_date'].astype(str).to_list())
    month_ax.tick_params(axis='x', rotation=60)
    st.pyplot(month_fig, width='stretch')
    
    # Revenue by hour
    st.header('Revenue each pickup hour')
    st.subheader('')
    Avg_fare_hour = mart_hourly.sort_values('revenue', ascending = False)
    
    fare_fig, fare_ax =  plt.subplots(figsize = (10,5))
    fare_ax.bar(Avg_fare_hour ['pickup_hour'],Avg_fare_hour ['revenue'])

    fare_ax.grid(True, alpha = 0.5, axis = 'y')
    fare_ax.set_xlabel('Hour')
    fare_ax.set_ylabel('Fare')
    fare_ax.set_xticks(Avg_fare_hour['pickup_hour'])
    
    st.pyplot(fare_fig, width='stretch')
    
elif page == 'Zone Analysis': 
    st.title('Zone Analysis')
    taxi_zone = pd.read_csv('../TAXINYC/data/raw/taxi_zone_lookup.csv')

    #-----Zone revenue
    st.title('Zone Revenue')
    taxi_zone = pd.read_csv('../TAXINYC/data/raw/taxi_zone_lookup.csv')

    mart_pickup = mart_pickup.merge(
        taxi_zone[['LocationID','Zone']],
        left_on='PULocationID',
        right_on='LocationID',
        how='left'
    ).rename(columns = {'Zone':'pickup_zone'}).drop(columns=['LocationID'])
    top_revenue_PU = mart_pickup.sort_values('total_amount', ascending= False).head(20)
    
    st.header('Top Pick Up Zone By Trips')
    PUR_fig, PUR_ax = plt.subplots(figsize = (10,5))

    PUR_ax.bar(top_revenue_PU['pickup_zone'].to_list(),top_revenue_PU ['total_amount'])

    PUR_ax.set_xlabel('Zone')
    PUR_ax.set_ylabel('Trips')
    PUR_ax.set_xticks(range(len(top_revenue_PU))) 
    PUR_ax.set_xticklabels(
        top_revenue_PU['pickup_zone'].astype(str), rotation = 45, ha='right'
    )
    
    st.pyplot(PUR_fig,  width='stretch')
    

    mart_pickup = mart_pickup.merge(
        taxi_zone[['LocationID','Zone']],
        left_on='PULocationID',
        right_on='LocationID',
        how='left'
    ).rename(columns = {'Zone':'pickup_zone'}).drop(columns=['LocationID'])
    top_PU = mart_pickup.head(20)
    
    st.header('Top Pick Up Zone By Trips')
    PU_fig, PU_ax = plt.subplots(figsize = (10,5))

    PU_ax.bar(top_PU['pickup_zone'].to_list(),top_PU ['trip_count'])

    PU_ax.set_xlabel('Zone')
    PU_ax.set_ylabel('Trips')
    PU_ax.set_xticks(range(len(top_PU))) 
    PU_ax.set_xticklabels(
        top_PU['pickup_zone'].astype(str), rotation = 45, ha='right'
    )
    
    st.pyplot(PU_fig,  width='stretch')
    
    
    #common drop off zones

    dropoff_common =  mart_dropoff.head(20)

    dropoff_common = dropoff_common.merge(
        taxi_zone[['LocationID','Zone']],
        left_on='DOLocationID',
        right_on='LocationID',
        how='left'
    ).rename(columns = {'Zone':'dropoff_zone'}).drop(columns=['LocationID'])

    st.header('Common Drop Off Zone By Trips')
    DO_fig, DO_ax = plt.subplots(figsize = (10,5))

    DO_ax.bar(dropoff_common['dropoff_zone'].to_list(),dropoff_common ['trip_count'])

    DO_ax.set_xlabel('Zone')
    DO_ax.set_ylabel('Trips')
    DO_ax.set_xticks(range(len(dropoff_common))) 
    DO_ax.set_xticklabels(
        dropoff_common['dropoff_zone'].astype(str), rotation = 45, ha='right'
    )
    
    st.pyplot(DO_fig,  width='stretch')
    
    #Top pickup dropoff pair

    pudo_pair = (
        db_clean.groupby(['PULocationID','DOLocationID'])
        .agg(trip_count = ('tpep_pickup_datetime','count'))
        .sort_values('trip_count', ascending = False)
        .reset_index()
    )

    pudo_pair = pudo_pair.merge(
        taxi_zone[['LocationID','Zone']],
        left_on='DOLocationID',
        right_on='LocationID',
        how='left'
    ).rename(columns = {'Zone':'dropoff_zone'}).drop(columns=['LocationID'])

    pudo_pair = pudo_pair.merge(
        taxi_zone[['LocationID','Zone']],
        left_on='PULocationID',
        right_on='LocationID',
        how='left'
    ).rename(columns = {'Zone':'pickup_zone'}).drop(columns=['LocationID'])

    pudo_pair['pair_name'] = pudo_pair['pickup_zone'] + pudo_pair['dropoff_zone']

    top_10_pair = pudo_pair.head(10)
    
    st.header('Top Pick up Drop off Pair by trips')
    st.subheader('')
    PUDO_fig, PUDO_ax = plt.subplots(figsize = (10,5))

    PUDO_ax.barh(top_10_pair['pair_name'].to_list(),top_10_pair ['trip_count'])

    PUDO_ax.set_xlabel('Trips Count')
    PUDO_ax.set_ylabel('Zone Pair')
    PUDO_ax.grid(True, alpha = 0.5, axis = 'x')
    
    st.pyplot(PUDO_fig,  width='stretch')

    
        
        
    
elif page == 'Tip Analysis':
    st.title('Tip Analysis')
    
    #Tip rate by payment
    st.header('Average Tip by Payment method')
    st.subheader('')
    
    tip_fig, tip_ax =  plt.subplots(figsize = (10,5))
    tip_ax.bar(mart_tip['payment_type'],mart_tip['avg_tip_rate'])

    tip_ax.grid(True, alpha = 0.5, axis = 'y')
    tip_ax.set_xlabel('Hour')
    tip_ax.set_ylabel('Tip Rate')
    tip_ax.set_xticks(mart_tip['payment_type'])
    
    st.pyplot(tip_fig, width='stretch')
    
    #-----
    st.header('Tip rate by hour')

    st.subheader('')
        
    hourtip_fig, hourtip_ax =  plt.subplots(figsize = (10,5))
    hourtip_ax.bar(mart_hourly['pickup_hour'],mart_hourly['avg_tip_rate'])

    hourtip_ax.grid(True, alpha = 0.5, axis = 'y')
    hourtip_ax.set_xlabel('Hour')
    hourtip_ax.set_ylabel('Tip Rat (%)')
    hourtip_ax.set_xticks(mart_hourly['pickup_hour'])
    
    st.pyplot(hourtip_fig, width='stretch')
    
    
elif page == 'Data Quality':
    st.title('Data Quality')
    
    total_trip = db_clean['tpep_pickup_datetime'].count()
    rejected_trip = db_rejected['tpep_pickup_datetime'].count()
    reject_pct = rejected_trip / (total_trip+rejected_trip) * 100
    
    col1, col2 = st.columns(2)
    
    col1.metric('Rejected Records', f'{rejected_trip:.0f}')
    col2.metric('Reject Percentage', f'{reject_pct:.2f}%')
    
    st.header('Data quality Problems')
    st.write(' **Unphysical Physics:** Negative or zero duration values (`tpep_dropoff_datetime` equal to or after `tpep_pickup_datetime`) and zero distances with possitive fare amounts.')
    st.write(' **Meter Logging Errors:** Negative or zero fare amounts, negative tips, or impossbile calculated speeds (>80mph).')
    st.write(" **Missing/Invalid Passenger Counts:** Records with '0' or 'NaN' passengers. ")
    
    st.header('Rejected Breakdown')
    st.markdown("""

    | Rejected Reason | Row Count | % of Raw Data | Business Justification |
    |:--- |:---|:---|:---|
    | Negative Or 0 Duration | 2051 | | |
    | Zero Distance trip | 90893 | | |
    | No passenger | 24656 | | |
    | Missing pickup dropoff location | 0 | | |
    """)
elif page == 'Anomaly Review':
    st.title('Anomaly Review')
    
    st.write(r"1. **Extreme Distance ($\geq$ 50 miles):** 490 records. Kept because these represent legitimate long_distance out-of-city charter trips")    
    st.write(r"2. **Extreme Fare ($\geq$ \$300):** 2865 records. Kept as valid high-value commercial runs")